"""TSFM-style trajectory head + v3 ensemble (HMM + GBM + TSFM).

Default backend is a causal local PatchTST-lite (numpy/sklearn) so the stack
runs offline without downloading multi-GB foundation checkpoints.

Optional: set KOSTOLANY_TSFM_BACKEND=chronos and install chronos-forecasting
to swap in a HuggingFace Chronos zero-shot forecaster for the trajectory arm.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge
from sklearn.preprocessing import StandardScaler

from kostolany.models import KostolanyGBM, KostolanyHMM, RegimePrediction
from kostolany.settings import get_settings


@dataclass
class TrajectoryForecast:
    """Multi-horizon forecast summary used for transition proximity."""

    index: pd.Index
    ret_hat: pd.DataFrame  # columns h1,h5,h20
    vol_hat: pd.Series
    embed: pd.DataFrame  # local patch embedding dims
    transition_score: pd.Series  # higher => regime change more likely soon


def _patch_matrix(x: np.ndarray, patch: int, stride: int) -> np.ndarray:
    """Causal patches ending at each t (no future samples inside a patch)."""
    n, d = x.shape
    rows = []
    for t in range(n):
        start = t - patch + 1
        if start < 0:
            pad = np.repeat(x[[0]], -start, axis=0)
            window = np.vstack([pad, x[: t + 1]])
        else:
            window = x[start : t + 1]
        # downsample by stride inside patch
        rows.append(window[::stride].reshape(-1))
    # equalize length
    maxlen = max(len(r) for r in rows)
    out = np.zeros((n, maxlen), dtype=float)
    for i, r in enumerate(rows):
        out[i, : len(r)] = r
    return out


class LocalTSFM:
    """Causal multi-horizon ridge forecaster on patched returns/volume features."""

    def __init__(self, patch: int = 32, stride: int = 2, horizons: tuple[int, ...] = (1, 5, 20)) -> None:
        self.patch = patch
        self.stride = stride
        self.horizons = horizons
        self.scalers_: dict[int, StandardScaler] = {}
        self.models_: dict[int, Ridge] = {}
        self.vol_model_: Ridge | None = None
        self.vol_scaler_: StandardScaler | None = None
        self.columns_: list[str] = []

    def _base_series(self, X: pd.DataFrame) -> pd.DataFrame:
        cols = [c for c in ("ret_20", "ret_60", "vol_z", "trend_slope", "drawdown", "sentiment_proxy") if c in X.columns]
        if not cols:
            cols = list(X.columns[:6])
        self.columns_ = cols
        return X[cols].astype(float)

    def fit(self, X: pd.DataFrame, y: pd.Series | None = None) -> "LocalTSFM":
        base = self._base_series(X).dropna()
        arr = base.to_numpy()
        patches = _patch_matrix(arr, self.patch, self.stride)
        # targets from future returns of ret_20-like first col or trend
        signal = base.iloc[:, 0]
        for h in self.horizons:
            target = signal.shift(-h)
            valid = target.notna().to_numpy()
            scaler = StandardScaler()
            Xp = scaler.fit_transform(patches[valid])
            model = Ridge(alpha=1.0)
            model.fit(Xp, target.to_numpy()[valid])
            self.scalers_[h] = scaler
            self.models_[h] = model
        # vol target: abs future return
        fut = signal.diff().shift(-5).abs()
        valid = fut.notna().to_numpy()
        self.vol_scaler_ = StandardScaler()
        Xp = self.vol_scaler_.fit_transform(patches[valid])
        self.vol_model_ = Ridge(alpha=1.0)
        self.vol_model_.fit(Xp, fut.to_numpy()[valid])
        return self

    def predict_trajectory(self, X: pd.DataFrame) -> TrajectoryForecast:
        base = self._base_series(X)
        idx = base.index
        arr = base.fillna(0.0).to_numpy()
        patches = _patch_matrix(arr, self.patch, self.stride)
        ret_hat = {}
        for h, model in self.models_.items():
            Xp = self.scalers_[h].transform(patches)
            ret_hat[f"h{h}"] = model.predict(Xp)
        ret_df = pd.DataFrame(ret_hat, index=idx)
        assert self.vol_model_ is not None and self.vol_scaler_ is not None
        vol = pd.Series(self.vol_model_.predict(self.vol_scaler_.transform(patches)), index=idx, name="vol_hat")
        # embedding = last patch flattened, PCA-less: take first 8 comps via mean blocks
        embed = pd.DataFrame(patches[:, :8], index=idx, columns=[f"e{i}" for i in range(8)])
        # transition score: disagreement across horizons + vol spike
        if {"h1", "h20"}.issubset(ret_df.columns):
            disagree = (ret_df["h1"] - ret_df["h20"]).abs()
        else:
            disagree = ret_df.std(axis=1)
        tscore = (0.6 * disagree.rank(pct=True) + 0.4 * vol.rank(pct=True)).rename("transition_score")
        return TrajectoryForecast(index=idx, ret_hat=ret_df, vol_hat=vol, embed=embed, transition_score=tscore)

    def regime_proba_from_trajectory(self, traj: TrajectoryForecast) -> pd.DataFrame:
        """Map trajectory sign/vol into soft 6-regime probabilities (prior head)."""
        h5 = traj.ret_hat.get("h5", traj.ret_hat.iloc[:, 0])
        up = (h5 > 0).astype(float)
        down = 1.0 - up
        hot = traj.transition_score.clip(0, 1)
        cool = 1.0 - hot
        # Allocate mass across cycle positions
        proba = pd.DataFrame(index=traj.index, columns=[f"p{i}" for i in range(6)], dtype=float)
        proba["p0"] = down * cool * 0.35 + up * cool * 0.15  # A1
        proba["p1"] = up * cool * 0.45  # A2
        proba["p2"] = up * hot * 0.55  # A3
        proba["p3"] = up * cool * 0.10 + down * cool * 0.20  # B1
        proba["p4"] = down * cool * 0.40  # B2
        proba["p5"] = down * hot * 0.55  # B3
        proba = proba.div(proba.sum(axis=1), axis=0).fillna(1 / 6)
        return proba


class ChronosTSFM:
    """Optional Chronos backend — degrades to LocalTSFM if import/model fails."""

    def __init__(self) -> None:
        self._local = LocalTSFM()
        self._pipe = None

    def fit(self, X: pd.DataFrame, y: pd.Series | None = None) -> "ChronosTSFM":
        self._local.fit(X, y)
        try:
            from chronos import ChronosPipeline  # type: ignore

            self._pipe = ChronosPipeline.from_pretrained(
                "amazon/chronos-t5-tiny",
                device_map="cpu",
            )
        except Exception:  # noqa: BLE001
            self._pipe = None
        return self

    def predict_trajectory(self, X: pd.DataFrame) -> TrajectoryForecast:
        base = self._local.predict_trajectory(X)
        if self._pipe is None:
            return base
        # Use Chronos only on the last window to adjust transition score (cost control)
        try:
            import torch

            series = X.iloc[:, 0].dropna().astype(float).tail(128).to_numpy()
            ctx = torch.tensor(series, dtype=torch.float32)
            forecast = self._pipe.predict(ctx, prediction_length=20)
            # forecast shape ~ (num_samples, pred_len)
            med = forecast.median(dim=0).values.numpy()
            slope = float(med[-1] - med[0])
            # blend transition score at the end
            adj = base.transition_score.copy()
            adj.iloc[-1] = float(np.clip(abs(slope) * 5 + adj.iloc[-1], 0, 1))
            return TrajectoryForecast(
                index=base.index,
                ret_hat=base.ret_hat,
                vol_hat=base.vol_hat,
                embed=base.embed,
                transition_score=adj,
            )
        except Exception:  # noqa: BLE001
            return base

    def regime_proba_from_trajectory(self, traj: TrajectoryForecast) -> pd.DataFrame:
        return self._local.regime_proba_from_trajectory(traj)


def build_tsfm():
    backend = get_settings().tsfm_backend.lower()
    if backend == "chronos":
        return ChronosTSFM()
    return LocalTSFM()


class TSFMEnsemble:
    """v3 ensemble: weighted HMM + GBM + TSFM trajectory prior."""

    def __init__(
        self,
        *,
        w_hmm: float = 0.30,
        w_gbm: float = 0.45,
        w_tsfm: float = 0.25,
        transition_soften: float = 0.15,
        gbm_kwargs: dict | None = None,
        hmm_kwargs: dict | None = None,
    ) -> None:
        self.w_hmm = w_hmm
        self.w_gbm = w_gbm
        self.w_tsfm = w_tsfm
        self.transition_soften = transition_soften
        self.hmm = KostolanyHMM(**(hmm_kwargs or {}))
        self.gbm = KostolanyGBM(**(gbm_kwargs or {}))
        self.tsfm = build_tsfm()
        self.last_traj_: TrajectoryForecast | None = None

    def fit(self, X: pd.DataFrame, y: pd.Series) -> "TSFMEnsemble":
        self.hmm.fit(X, y)
        self.gbm.fit(X, y)
        # Fit TSFM on feature matrix only (no labels — representation/forecast)
        feat_cols = [c for c in X.columns]
        self.tsfm.fit(X[feat_cols], None)
        return self

    def predict(self, X: pd.DataFrame) -> RegimePrediction:
        a = self.hmm.predict(X)
        b = self.gbm.predict(X)
        traj = self.tsfm.predict_trajectory(X)
        self.last_traj_ = traj
        c = self.tsfm.regime_proba_from_trajectory(traj)
        common = a.proba.index.intersection(b.proba.index).intersection(c.index)
        proba = (
            self.w_hmm * a.proba.loc[common]
            + self.w_gbm * b.proba.loc[common]
            + self.w_tsfm * c.loc[common]
        )
        # Boost uncertainty near high transition score
        t = traj.transition_score.reindex(common).fillna(0.0)
        soft = self.transition_soften
        blend = proba.mul(1.0 - soft * t, axis=0).add((soft * t / 6.0), axis=0)
        blend = blend.div(blend.sum(axis=1), axis=0)
        regimes = blend.idxmax(axis=1).map(lambda col: int(col[1:])).rename("regime")
        return RegimePrediction(
            regimes=regimes,
            proba=blend,
            transition_matrix=a.transition_matrix,
            mapping=a.mapping,
        )

    def fit_predict(
        self, X_train: pd.DataFrame, y_train: pd.Series, X_test: pd.DataFrame
    ) -> tuple[pd.Series, pd.DataFrame]:
        self.fit(X_train, y_train)
        pred = self.predict(X_test)
        return pred.regimes.reindex(X_test.index).ffill().bfill(), pred.proba.reindex(X_test.index)
