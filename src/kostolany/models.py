"""Regime detection models: multivariate Gaussian HMM + optional LightGBM head."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from hmmlearn.hmm import GaussianHMM
from sklearn.calibration import CalibratedClassifierCV
from sklearn.preprocessing import LabelEncoder

from kostolany.labels import map_hmm_states_to_regimes
from kostolany.regimes import Regime


@dataclass
class RegimePrediction:
    regimes: pd.Series
    proba: pd.DataFrame
    state_proba: pd.DataFrame | None = None
    transition_matrix: np.ndarray | None = None
    mapping: dict[int, int] = field(default_factory=dict)


class KostolanyHMM:
    """Multivariate Gaussian HMM with Kostolany regime mapping."""

    def __init__(
        self,
        n_states: int = 6,
        n_iter: int = 200,
        covariance_type: str = "diag",
        random_state: int = 42,
    ) -> None:
        self.n_states = n_states
        self.n_iter = n_iter
        self.covariance_type = covariance_type
        self.random_state = random_state
        self.model_: GaussianHMM | None = None
        self.mapping_: dict[int, int] = {}
        self.columns_: list[str] = []

    def fit(self, X: pd.DataFrame, y: pd.Series | None = None) -> "KostolanyHMM":
        Xc = X.dropna()
        self.columns_ = list(Xc.columns)
        arr = Xc.to_numpy(dtype=float)
        model = GaussianHMM(
            n_components=self.n_states,
            covariance_type=self.covariance_type,
            n_iter=self.n_iter,
            random_state=self.random_state,
            verbose=False,
        )
        model.fit(arr)
        self.model_ = model
        states = model.predict(arr)
        self.mapping_ = map_hmm_states_to_regimes(states, Xc, self.n_states)

        # If weak labels provided, refine mapping by majority vote (still no gold)
        if y is not None:
            yy = y.reindex(Xc.index).dropna().astype(int)
            st = pd.Series(states, index=Xc.index).reindex(yy.index)
            refined = {}
            for s in range(self.n_states):
                mask = st == s
                if mask.any():
                    refined[s] = int(yy.loc[mask].mode().iloc[0])
                else:
                    refined[s] = self.mapping_.get(s, int(Regime.A2))
            self.mapping_ = refined
        return self

    def predict(self, X: pd.DataFrame) -> RegimePrediction:
        if self.model_ is None:
            raise RuntimeError("Model not fitted")
        Xc = X[self.columns_].dropna()
        arr = Xc.to_numpy(dtype=float)
        states = self.model_.predict(arr)
        post = self.model_.predict_proba(arr)

        regimes = pd.Series([self.mapping_[int(s)] for s in states], index=Xc.index, name="regime")

        # Aggregate HMM state posterior into 6-regime probabilities
        proba = np.zeros((len(Xc), 6), dtype=float)
        for s, regime in self.mapping_.items():
            proba[:, int(regime)] += post[:, s]
        # Normalize
        proba = proba / np.clip(proba.sum(axis=1, keepdims=True), 1e-12, None)
        proba_df = pd.DataFrame(proba, index=Xc.index, columns=[f"p{i}" for i in range(6)])
        state_df = pd.DataFrame(post, index=Xc.index, columns=[f"s{i}" for i in range(self.n_states)])

        return RegimePrediction(
            regimes=regimes,
            proba=proba_df,
            state_proba=state_df,
            transition_matrix=self.model_.transmat_.copy(),
            mapping=dict(self.mapping_),
        )

    def fit_predict(
        self, X_train: pd.DataFrame, y_train: pd.Series, X_test: pd.DataFrame
    ) -> tuple[pd.Series, pd.DataFrame]:
        self.fit(X_train, y_train)
        pred = self.predict(X_test)
        return pred.regimes.reindex(X_test.index).ffill().bfill(), pred.proba.reindex(X_test.index)


class KostolanyGBM:
    """LightGBM classifier on weak labels with probability calibration."""

    def __init__(
        self,
        calibrate: bool = True,
        random_state: int = 42,
        *,
        n_estimators: int = 400,
        learning_rate: float = 0.03,
        num_leaves: int = 47,
        subsample: float = 0.8,
        colsample_bytree: float = 0.8,
    ) -> None:
        self.calibrate = calibrate
        self.random_state = random_state
        self.n_estimators = n_estimators
        self.learning_rate = learning_rate
        self.num_leaves = num_leaves
        self.subsample = subsample
        self.colsample_bytree = colsample_bytree
        self.model_ = None
        self.le_ = LabelEncoder()
        self.columns_: list[str] = []

    def fit(self, X: pd.DataFrame, y: pd.Series) -> "KostolanyGBM":
        import lightgbm as lgb

        df = X.join(y.rename("y")).dropna()
        self.columns_ = [c for c in X.columns]
        y_enc = self.le_.fit_transform(df["y"].astype(int))
        base = lgb.LGBMClassifier(
            n_estimators=self.n_estimators,
            learning_rate=self.learning_rate,
            max_depth=-1,
            num_leaves=self.num_leaves,
            subsample=self.subsample,
            colsample_bytree=self.colsample_bytree,
            random_state=self.random_state,
            verbose=-1,
        )
        if self.calibrate and len(df) > 200:
            self.model_ = CalibratedClassifierCV(base, method="isotonic", cv=3)
        else:
            self.model_ = base
        self.model_.fit(df[self.columns_], y_enc)
        return self

    def predict(self, X: pd.DataFrame) -> RegimePrediction:
        if self.model_ is None:
            raise RuntimeError("Model not fitted")
        Xc = X[self.columns_].dropna()
        proba_raw = self.model_.predict_proba(Xc)
        classes = self.le_.classes_.astype(int)
        full = np.zeros((len(Xc), 6), dtype=float)
        for j, c in enumerate(classes):
            full[:, int(c)] = proba_raw[:, j]
        full = full / np.clip(full.sum(axis=1, keepdims=True), 1e-12, None)
        regimes = pd.Series(full.argmax(axis=1), index=Xc.index, name="regime")
        proba_df = pd.DataFrame(full, index=Xc.index, columns=[f"p{i}" for i in range(6)])
        return RegimePrediction(regimes=regimes, proba=proba_df)

    def fit_predict(
        self, X_train: pd.DataFrame, y_train: pd.Series, X_test: pd.DataFrame
    ) -> tuple[pd.Series, pd.DataFrame]:
        self.fit(X_train, y_train)
        pred = self.predict(X_test)
        return pred.regimes.reindex(X_test.index).ffill().bfill(), pred.proba.reindex(X_test.index)


class EnsembleEngine:
    """Average HMM + GBM probabilities (simple, calibrated ensemble)."""

    def __init__(self) -> None:
        self.hmm = KostolanyHMM()
        self.gbm = KostolanyGBM()

    def fit(self, X: pd.DataFrame, y: pd.Series) -> "EnsembleEngine":
        self.hmm.fit(X, y)
        self.gbm.fit(X, y)
        return self

    def predict(self, X: pd.DataFrame) -> RegimePrediction:
        a = self.hmm.predict(X)
        b = self.gbm.predict(X)
        common = a.proba.index.intersection(b.proba.index)
        proba = 0.55 * a.proba.loc[common] + 0.45 * b.proba.loc[common]
        proba = proba.div(proba.sum(axis=1), axis=0)
        regimes = proba.idxmax(axis=1).map(lambda c: int(c[1:])).rename("regime")
        return RegimePrediction(
            regimes=regimes,
            proba=proba,
            transition_matrix=a.transition_matrix,
            mapping=a.mapping,
        )

    def fit_predict(
        self, X_train: pd.DataFrame, y_train: pd.Series, X_test: pd.DataFrame
    ) -> tuple[pd.Series, pd.DataFrame]:
        self.fit(X_train, y_train)
        pred = self.predict(X_test)
        return pred.regimes.reindex(X_test.index).ffill().bfill(), pred.proba.reindex(X_test.index)
