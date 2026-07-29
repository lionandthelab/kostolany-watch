"""Regime detection models: multivariate Gaussian HMM + optional LightGBM head."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from hmmlearn.hmm import GaussianHMM
from sklearn.preprocessing import LabelEncoder

from kostolany.labels import map_hmm_states_to_regimes
from kostolany.regimes import Regime

TARGET_ONLY_COLUMNS = {"log_ret_1"}


@dataclass
class RegimePrediction:
    regimes: pd.Series
    proba: pd.DataFrame
    state_proba: pd.DataFrame | None = None
    transition_matrix: np.ndarray | None = None
    mapping: dict[int, int] = field(default_factory=dict)


def causal_cycle_filter(proba: pd.DataFrame, strength: float = 0.22) -> pd.DataFrame:
    """Causally smooth probabilities with the Kostolany cycle prior.

    Only the previous filtered row influences the current row; no centered
    windows or backward pass are used. This reduces one-day regime flicker
    without leaking future observations.
    """
    if proba.empty or strength <= 0:
        return proba
    cols = [f"p{i}" for i in range(6)]
    arr = proba.reindex(columns=cols, fill_value=0.0).to_numpy(dtype=float)
    arr /= np.clip(arr.sum(axis=1, keepdims=True), 1e-12, None)

    transition = np.full((6, 6), 0.01 / 4.0, dtype=float)
    for i in range(6):
        transition[i, i] = 0.84
        transition[i, (i + 1) % 6] = 0.12
        transition[i, (i - 1) % 6] = 0.03
    transition /= transition.sum(axis=1, keepdims=True)

    out = np.zeros_like(arr)
    out[0] = arr[0]
    for i in range(1, len(arr)):
        prior = out[i - 1] @ transition
        row = (1.0 - strength) * arr[i] + strength * prior
        out[i] = row / np.clip(row.sum(), 1e-12, None)
    return pd.DataFrame(out, index=proba.index, columns=cols)


def _assign_states_to_regimes(
    states: pd.Series,
    weak: pd.Series,
    n_states: int,
    *,
    fallback: dict[int, int] | None = None,
) -> dict[int, int]:
    """Bijective state->regime assignment maximizing weak-label agreement.

    Builds the state x regime co-occurrence matrix and solves it as a linear
    assignment problem, so every regime is claimed by at most one state and —
    when ``n_states >= 6`` — every regime is reachable. States with no support
    fall back to the prototype mapping.
    """
    from scipy.optimize import linear_sum_assignment

    fallback = fallback or {}
    counts = np.zeros((n_states, 6), dtype=float)
    for state, regime in zip(states.to_numpy(), weak.to_numpy()):
        if np.isnan(state):
            continue
        counts[int(state), int(regime)] += 1.0

    # linear_sum_assignment minimizes; negate to maximize agreement.
    rows, cols = linear_sum_assignment(-counts)
    mapping = {int(s): int(r) for s, r in zip(rows, cols)}

    # Any state left unassigned (n_states > 6) keeps its prototype regime.
    for state in range(n_states):
        if state not in mapping:
            mapping[state] = int(fallback.get(state, int(Regime.A2)))
    return mapping


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
        Xc = X.drop(
            columns=[c for c in TARGET_ONLY_COLUMNS if c in X.columns]
        ).dropna()
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

        # If weak labels provided, refine the mapping (still no gold). An
        # unconstrained per-state majority vote lets several states claim the
        # same regime, which leaves the remaining regimes unreachable: predict()
        # only sums posterior mass into mapped regimes, so an unclaimed regime
        # is structurally probability-zero and the served posterior saturates.
        # Solve the state->regime assignment as a bijection instead.
        if y is not None:
            yy = y.reindex(Xc.index).dropna().astype(int)
            st = pd.Series(states, index=Xc.index).reindex(yy.index)
            self.mapping_ = _assign_states_to_regimes(
                st, yy, self.n_states, fallback=self.mapping_
            )
        return self

    def predict(self, X: pd.DataFrame) -> RegimePrediction:
        if self.model_ is None:
            raise RuntimeError("Model not fitted")
        Xc = X[self.columns_].dropna()
        arr = Xc.to_numpy(dtype=float)
        # hmmlearn's predict_proba is a forward-backward smoother: posterior
        # at t uses observations after t. Use a forward-only filter so replay
        # and OOS probabilities are point-in-time causal.
        log_emission = self.model_._compute_log_likelihood(arr)  # noqa: SLF001
        post = np.zeros((len(arr), self.n_states), dtype=float)
        previous = np.asarray(self.model_.startprob_, dtype=float)
        for i, log_like in enumerate(log_emission):
            prior = (
                previous
                if i == 0
                else previous @ np.asarray(self.model_.transmat_, dtype=float)
            )
            log_weight = np.log(np.clip(prior, 1e-300, None)) + log_like
            log_weight -= np.max(log_weight)
            row = np.exp(log_weight)
            row /= np.clip(row.sum(), 1e-300, None)
            post[i] = row
            previous = row
        states = post.argmax(axis=1)

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
        context = pd.concat([X_train.tail(126), X_test])
        pred = self.predict(context)
        return pred.regimes.reindex(X_test.index).ffill().bfill(), pred.proba.reindex(X_test.index)


class KostolanyGBM:
    """LightGBM classifier with temporal weak-label calibration."""

    def __init__(
        self,
        calibrate: bool = True,
        random_state: int = 42,
        *,
        n_estimators: int = 500,
        learning_rate: float = 0.028,
        num_leaves: int = 55,
        subsample: float = 0.82,
        colsample_bytree: float = 0.78,
        cycle_smooth: float = 0.18,
    ) -> None:
        self.calibrate = calibrate
        self.random_state = random_state
        self.n_estimators = n_estimators
        self.learning_rate = learning_rate
        self.num_leaves = num_leaves
        self.subsample = subsample
        self.colsample_bytree = colsample_bytree
        self.cycle_smooth = cycle_smooth
        self.model_ = None
        self.le_ = LabelEncoder()
        self.columns_: list[str] = []
        self.temperature_: float = 1.0

    def _base_model(self):
        import lightgbm as lgb

        return lgb.LGBMClassifier(
            n_estimators=self.n_estimators,
            learning_rate=self.learning_rate,
            max_depth=-1,
            num_leaves=self.num_leaves,
            min_child_samples=35,
            subsample=self.subsample,
            subsample_freq=1,
            colsample_bytree=self.colsample_bytree,
            reg_alpha=0.15,
            reg_lambda=2.5,
            class_weight="balanced",
            random_state=self.random_state,
            n_jobs=1,
            verbose=-1,
        )

    def fit(self, X: pd.DataFrame, y: pd.Series) -> "KostolanyGBM":
        self.columns_ = [c for c in X.columns if c not in TARGET_ONLY_COLUMNS]
        df = X[self.columns_].join(y.rename("y")).dropna()
        y_enc = self.le_.fit_transform(df["y"].astype(int))
        self.temperature_ = 1.0

        # Time-ordered calibration: train on the prefix, calibrate on the
        # trailing slice. Unlike CalibratedClassifierCV(cv=3), this never fits
        # a fold using observations that occur after its calibration rows.
        if self.calibrate and len(df) >= 500:
            cut = max(300, int(len(df) * 0.80))
            pre = self._base_model()
            pre.fit(df[self.columns_].iloc[:cut], y_enc[:cut])
            raw = pre.predict_proba(df[self.columns_].iloc[cut:])
            raw_classes = np.asarray(pre.classes_, dtype=int)
            full = np.full((len(raw), len(self.le_.classes_)), 1e-9, dtype=float)
            for j, cls in enumerate(raw_classes):
                full[:, cls] = raw[:, j]
            full /= np.clip(full.sum(axis=1, keepdims=True), 1e-12, None)
            y_cal = y_enc[cut:]
            known = np.isin(y_cal, raw_classes)
            if int(known.sum()) >= 30:
                full = full[known]
                y_cal = y_cal[known]
                temperatures = np.linspace(0.65, 2.75, 43)
                losses = []
                for temp in temperatures:
                    scaled = np.power(
                        np.clip(full, 1e-9, 1.0), 1.0 / temp
                    )
                    scaled /= np.clip(
                        scaled.sum(axis=1, keepdims=True), 1e-12, None
                    )
                    losses.append(
                        float(
                            -np.mean(
                                np.log(
                                    np.clip(
                                        scaled[
                                            np.arange(len(y_cal)), y_cal
                                        ],
                                        1e-12,
                                        1.0,
                                    )
                                )
                            )
                        )
                    )
                self.temperature_ = float(
                    temperatures[int(np.argmin(losses))]
                )

        self.model_ = self._base_model()
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
        if self.temperature_ != 1.0:
            full = np.power(np.clip(full, 1e-12, 1.0), 1.0 / self.temperature_)
        full = full / np.clip(full.sum(axis=1, keepdims=True), 1e-12, None)
        proba_df = pd.DataFrame(full, index=Xc.index, columns=[f"p{i}" for i in range(6)])
        proba_df = causal_cycle_filter(proba_df, strength=self.cycle_smooth)
        regimes = pd.Series(
            proba_df.to_numpy().argmax(axis=1), index=Xc.index, name="regime"
        )
        return RegimePrediction(regimes=regimes, proba=proba_df)

    def fit_predict(
        self, X_train: pd.DataFrame, y_train: pd.Series, X_test: pd.DataFrame
    ) -> tuple[pd.Series, pd.DataFrame]:
        self.fit(X_train, y_train)
        context = pd.concat([X_train.tail(126), X_test])
        pred = self.predict(context)
        return pred.regimes.reindex(X_test.index).ffill().bfill(), pred.proba.reindex(X_test.index)


class EnsembleEngine:
    """Average HMM + GBM probabilities (simple, calibrated ensemble)."""

    def __init__(self) -> None:
        self.hmm = KostolanyHMM()
        self.gbm = KostolanyGBM(cycle_smooth=0.0)

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
        proba = causal_cycle_filter(proba, strength=0.12)
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
        context = pd.concat([X_train.tail(126), X_test])
        pred = self.predict(context)
        return pred.regimes.reindex(X_test.index).ffill().bfill(), pred.proba.reindex(X_test.index)
