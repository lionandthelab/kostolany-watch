"""TSFM-style trajectory head + v3 ensemble (HMM + GBM + TSFM).

Default backend is a causal local PatchTST-lite (numpy/sklearn) so the stack
runs offline without downloading multi-GB foundation checkpoints.

Optional: set KOSTOLANY_TSFM_BACKEND=chronos and install chronos-forecasting
to swap in a HuggingFace Chronos zero-shot forecaster for the trajectory arm.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from kostolany.models import (
    KostolanyGBM,
    KostolanyHMM,
    RegimePrediction,
    causal_cycle_filter,
)
from kostolany.settings import get_settings


@dataclass
class TrajectoryForecast:
    """Multi-horizon forecast summary used for transition proximity."""

    index: pd.Index
    ret_hat: pd.DataFrame  # cumulative simple returns: h1,h5,h20,h63
    vol_hat: pd.Series
    embed: pd.DataFrame  # local patch embedding dims
    transition_score: pd.Series  # higher => regime change more likely soon
    quantiles: pd.DataFrame | None = None  # h63_q10 / h63_q90
    direction_proba: pd.Series | None = None  # calibrated P(h63 return > 0)


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
    """Direct causal multi-horizon LightGBM forecaster.

    Learns forward cumulative log-returns from ``log_ret_1`` inside the training
    slice only. Includes a dedicated direction classifier so sign and magnitude
    can be estimated separately (commercial-v5).
    """

    def __init__(
        self,
        patch: int = 48,
        stride: int = 4,
        horizons: tuple[int, ...] = (1, 5, 20, 63),
        *,
        n_estimators: int = 360,
        learning_rate: float = 0.022,
        num_leaves: int = 31,
        direction_blend: float = 0.55,
        random_state: int = 42,
    ) -> None:
        self.patch = patch
        self.stride = stride
        self.horizons = horizons
        self.n_estimators = n_estimators
        self.learning_rate = learning_rate
        self.num_leaves = num_leaves
        self.direction_blend = float(np.clip(direction_blend, 0.0, 1.0))
        self.random_state = random_state
        self.models_: dict[int, Any] = {}
        self.quantile_models_: dict[tuple[int, float], Any] = {}
        self.direction_model_: Any | None = None
        self.vol_model_: Any | None = None
        self.columns_: list[str] = []
        self.disagreement_scale_: float = 1e-4
        self.vol_scale_: float = 0.02
        self.conformal_radius_: float = 0.0
        self.long_magnitude_scale_: float = 1.0

    def _base_series(self, X: pd.DataFrame, *, fit: bool = False) -> pd.DataFrame:
        preferred = (
            "log_ret_1",
            "ret_5",
            "ret_10",
            "ret_20",
            "ret_60",
            "rv_10",
            "rv_60",
            "downside_vol_20",
            "vol_z",
            "turnover_z",
            "vol_surge",
            "volume_price_corr",
            "breadth_proxy",
            "participation_trend",
            "trend_slope",
            "trend_accel",
            "drawdown",
            "ma_gap",
            "range_pos_252",
            "money_proxy",
            "credit_proxy",
            "sentiment_proxy",
        )
        if fit:
            self.columns_ = [c for c in preferred if c in X.columns]
            if "log_ret_1" not in self.columns_:
                raise ValueError("LocalTSFM requires raw log_ret_1 in the model matrix")
        if not self.columns_:
            raise RuntimeError("LocalTSFM has no fitted feature columns")
        return X.reindex(columns=self.columns_).astype(float)

    def _design(self, base: pd.DataFrame) -> np.ndarray:
        arr = base.to_numpy(dtype=float)
        patches = _patch_matrix(arr, self.patch, self.stride)
        n_steps = max(1, int(np.ceil(self.patch / self.stride)))
        p3 = patches.reshape(len(patches), n_steps, len(self.columns_))
        summary = np.hstack(
            [
                np.nanmean(p3, axis=1),
                np.nanstd(p3, axis=1),
                p3[:, -1, :] - p3[:, 0, :],
            ]
        )
        # Multi-scale causal return/vol stats from log_ret_1 (no future peek).
        log_ret = base["log_ret_1"].astype(float)
        parts: list[pd.Series] = []
        for window in (5, 20, 60, 126):
            min_p = max(3, window // 4)
            parts.append(log_ret.rolling(window, min_periods=min_p).mean())
            parts.append(log_ret.rolling(window, min_periods=min_p).std())
            parts.append(log_ret.rolling(window, min_periods=min_p).sum())
        multi_arr = pd.concat(parts, axis=1).to_numpy(dtype=float)
        design = np.hstack([patches, summary, np.nan_to_num(multi_arr, nan=0.0)])
        return np.nan_to_num(design, nan=0.0, posinf=6.0, neginf=-6.0)

    def _regressor(self, *, objective: str = "huber", alpha: float | None = None):
        import lightgbm as lgb

        kwargs: dict[str, Any] = {
            "objective": objective,
            "n_estimators": self.n_estimators,
            "learning_rate": self.learning_rate,
            "num_leaves": self.num_leaves,
            "max_depth": 6,
            "min_child_samples": 45,
            "subsample": 0.82,
            "subsample_freq": 1,
            "colsample_bytree": 0.78,
            "reg_alpha": 0.25,
            "reg_lambda": 4.0,
            "random_state": self.random_state,
            "n_jobs": 1,
            "verbosity": -1,
        }
        if alpha is not None:
            kwargs["alpha"] = alpha
        return lgb.LGBMRegressor(**kwargs)

    def fit(self, X: pd.DataFrame, y: pd.Series | None = None) -> "LocalTSFM":
        del y  # self-supervised forward-return targets; never regime gold
        base = self._base_series(X, fit=True).dropna()
        design = self._design(base)
        log_ret = base["log_ret_1"].astype(float)
        train_predictions: dict[int, np.ndarray] = {}
        max_h = max(self.horizons)

        for h in self.horizons:
            # At row t this is sum(log-return[t+1:t+h]). Because ``base`` is
            # already the training slice, targets crossing its end are NaN.
            target = log_ret.rolling(h, min_periods=h).sum().shift(-h)
            valid = target.notna().to_numpy()
            if int(valid.sum()) < max(120, self.num_leaves * 4):
                raise ValueError(f"Not enough causal rows for h{h}: {int(valid.sum())}")
            values = target.to_numpy(dtype=float)[valid]
            lo, hi = np.quantile(values, [0.005, 0.995])
            values = np.clip(values, lo, hi)
            model = self._regressor()
            model.fit(design[valid], values)
            self.models_[h] = model
            train_predictions[h] = model.predict(design)

            if h == max_h:
                # Out-of-time uncertainty calibration on the trailing slice.
                # The pre-model sees only targets ending before ``cut``.
                cut = max(420, int(len(base) * 0.80))
                row_no = np.arange(len(base))
                pre_valid = valid & (row_no < cut - h)
                cal_valid = valid & (row_no >= cut)
                if int(pre_valid.sum()) >= 250 and int(cal_valid.sum()) >= 40:
                    pre_model = self._regressor()
                    pre_target = target.to_numpy(dtype=float)[pre_valid]
                    pre_lo, pre_hi = np.quantile(pre_target, [0.005, 0.995])
                    pre_model.fit(
                        design[pre_valid],
                        np.clip(pre_target, pre_lo, pre_hi),
                    )
                    direct_cal = pre_model.predict(design[cal_valid])
                    cal_y = target.to_numpy(dtype=float)[cal_valid]
                    scales = np.linspace(0.35, 1.10, 31)
                    losses = [
                        float(
                            np.mean(
                                np.abs(
                                    scale * direct_cal - cal_y
                                )
                            )
                        )
                        for scale in scales
                    ]
                    self.long_magnitude_scale_ = float(
                        scales[int(np.argmin(losses))]
                    )
                    calibrated = self.long_magnitude_scale_ * direct_cal
                    self.conformal_radius_ = float(
                        np.quantile(np.abs(cal_y - calibrated), 0.80)
                    )

                for q in (0.10, 0.90):
                    q_model = self._regressor(objective="quantile", alpha=q)
                    q_model.fit(design[valid], values)
                    self.quantile_models_[(h, q)] = q_model

                # Dedicated up/down classifier for the commercial horizon.
                import lightgbm as lgb

                direction = lgb.LGBMClassifier(
                    objective="binary",
                    n_estimators=max(180, self.n_estimators // 2),
                    learning_rate=min(0.05, self.learning_rate * 1.5),
                    num_leaves=max(15, self.num_leaves - 4),
                    max_depth=6,
                    min_child_samples=40,
                    subsample=0.85,
                    subsample_freq=1,
                    colsample_bytree=0.8,
                    reg_alpha=0.2,
                    reg_lambda=3.0,
                    class_weight="balanced",
                    random_state=self.random_state,
                    n_jobs=1,
                    verbosity=-1,
                )
                direction.fit(design[valid], (values > 0).astype(int))
                self.direction_model_ = direction

        # Future 20d root-sum-square return is a direct uncertainty target.
        vol_target = (
            log_ret.pow(2).rolling(20, min_periods=20).sum().shift(-20).pow(0.5)
        )
        valid = vol_target.notna().to_numpy()
        self.vol_model_ = self._regressor(objective="regression_l1")
        self.vol_model_.fit(design[valid], vol_target.to_numpy(dtype=float)[valid])

        # Fixed train-only scales avoid rank(pct=True), which leaked future
        # rows when transition scores were calculated over a full history.
        daily = np.column_stack(
            [train_predictions[h] / max(1, h) for h in self.horizons]
        )
        disagreement = np.nanstd(daily, axis=1)
        self.disagreement_scale_ = max(
            1e-5, float(np.nanquantile(disagreement, 0.75))
        )
        vol_pred = np.maximum(0.0, self.vol_model_.predict(design))
        self.vol_scale_ = max(1e-4, float(np.nanmedian(vol_pred)))
        return self

    def predict_trajectory(self, X: pd.DataFrame) -> TrajectoryForecast:
        if not self.models_ or self.vol_model_ is None:
            raise RuntimeError("LocalTSFM not fitted")
        base = self._base_series(X).dropna()
        idx = base.index
        design = self._design(base)
        ret_hat: dict[str, np.ndarray] = {}
        raw_log_hat: dict[int, np.ndarray] = {}
        model_log_hat: dict[int, np.ndarray] = {}
        max_h = max(self.horizons)
        direction_proba = None
        if self.direction_model_ is not None:
            direction_proba = pd.Series(
                self.direction_model_.predict_proba(design)[:, 1],
                index=idx,
                name="p_up_h63",
            )

        for h, model in self.models_.items():
            log_hat = model.predict(design)
            model_log_hat[h] = log_hat.copy()
            if h == max_h:
                scaled = self.long_magnitude_scale_ * log_hat
                if direction_proba is not None and self.direction_blend > 0:
                    # Soft expected log-return from P(up), shrunk toward 0.5
                    # to avoid chronic bull bias in long equity bull markets.
                    p_up = direction_proba.to_numpy(dtype=float)
                    p_shrunk = 0.5 + 0.65 * (p_up - 0.5)
                    soft_ev = (2.0 * p_shrunk - 1.0) * np.abs(scaled)
                    log_hat = (
                        self.direction_blend * soft_ev
                        + (1.0 - self.direction_blend) * scaled
                    )
                else:
                    log_hat = scaled
            raw_log_hat[h] = log_hat.copy()
            ret_hat[f"h{h}"] = np.expm1(np.clip(log_hat, -1.2, 1.5))
        ret_df = pd.DataFrame(ret_hat, index=idx)
        vol = pd.Series(
            np.maximum(0.0, self.vol_model_.predict(design)),
            index=idx,
            name="vol_hat",
        )
        embed = pd.DataFrame(
            design[:, :8], index=idx, columns=[f"e{i}" for i in range(8)]
        )

        daily = np.column_stack(
            [
                np.log1p(ret_df[f"h{h}"].clip(lower=-0.999)).to_numpy()
                / max(1, h)
                for h in self.horizons
            ]
        )
        disagreement = np.nanstd(daily, axis=1)
        disagree_score = 1.0 - np.exp(
            -np.maximum(0.0, disagreement) / self.disagreement_scale_
        )
        vol_score = 1.0 - np.exp(-vol.to_numpy() / self.vol_scale_)
        tscore = pd.Series(
            np.clip(0.65 * disagree_score + 0.35 * vol_score, 0.0, 1.0),
            index=idx,
            name="transition_score",
        )

        quantiles = None
        if self.quantile_models_:
            q_values: dict[str, np.ndarray] = {}
            for (_, q), model in self.quantile_models_.items():
                # Preserve the raw quantile asymmetry around the calibrated
                # sign/magnitude median.
                raw_delta = model.predict(design) - model_log_hat[max_h]
                q_log = (
                    raw_log_hat[max_h]
                    + self.long_magnitude_scale_ * raw_delta
                )
                if q <= 0.5:
                    q_log = np.minimum(
                        q_log,
                        raw_log_hat[max_h]
                        - self.conformal_radius_,
                    )
                else:
                    q_log = np.maximum(
                        q_log,
                        raw_log_hat[max_h]
                        + self.conformal_radius_,
                    )
                q_values[f"h{max_h}_q{int(q * 100):02d}"] = np.expm1(
                    np.clip(q_log, -1.2, 1.5)
                )
            quantiles = pd.DataFrame(q_values, index=idx)
            lo_col, hi_col = f"h{max_h}_q10", f"h{max_h}_q90"
            if {lo_col, hi_col}.issubset(quantiles.columns):
                lo = np.minimum(quantiles[lo_col], quantiles[hi_col])
                hi = np.maximum(quantiles[lo_col], quantiles[hi_col])
                quantiles[lo_col], quantiles[hi_col] = lo, hi

        return TrajectoryForecast(
            index=idx,
            ret_hat=ret_df,
            vol_hat=vol,
            embed=embed,
            transition_score=tscore,
            quantiles=quantiles,
            direction_proba=direction_proba,
        )

    def regime_proba_from_trajectory(self, traj: TrajectoryForecast) -> pd.DataFrame:
        """Map direct horizon shape into a soft lifecycle prior.

        This head is deliberately low-weight in ``TSFMEnsemble``; it contributes
        forward trajectory information without pretending future returns are
        regime labels.
        """
        h5 = traj.ret_hat.get("h5", traj.ret_hat.iloc[:, 0]).fillna(0.0)
        h20 = traj.ret_hat.get("h20", h5).fillna(0.0)
        h63 = traj.ret_hat.get("h63", h20).fillna(0.0)
        if traj.direction_proba is not None:
            # Nudge long-horizon prior with calibrated P(up).
            tilt = (traj.direction_proba.reindex(h63.index).fillna(0.5) - 0.5) * 0.35
            h63 = h63 + tilt
        hot = traj.transition_score.clip(0, 1).fillna(0.5)
        scale = traj.vol_hat.clip(lower=0.01)

        short = np.tanh(h5 / scale)
        mid = np.tanh(h20 / (scale * np.sqrt(4.0)))
        long = np.tanh(h63 / (scale * np.sqrt(12.6)))
        turn = hot * 2.0 - 1.0

        scores = np.column_stack(
            [
                0.9 * long - 0.5 * mid + 0.5 * turn,  # A1: recovery
                0.8 * mid + 0.9 * long - 0.4 * turn,  # A2: participation
                0.9 * short + 0.5 * mid + 0.7 * turn - 0.3 * long,  # A3
                0.4 * short - 0.8 * long + 0.6 * turn,  # B1: rollover
                -0.8 * mid - 0.9 * long - 0.3 * turn,  # B2: decline
                -0.9 * short - 0.4 * mid + 0.5 * long + 0.8 * turn,  # B3
            ]
        )
        scores -= np.max(scores, axis=1, keepdims=True)
        probs = np.exp(scores)
        probs /= np.clip(probs.sum(axis=1, keepdims=True), 1e-12, None)
        return pd.DataFrame(
            probs, index=traj.index, columns=[f"p{i}" for i in range(6)]
        )


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
                quantiles=base.quantiles,
                direction_proba=base.direction_proba,
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
        w_hmm: float = 0.25,
        w_gbm: float = 0.55,
        w_tsfm: float = 0.20,
        transition_soften: float = 0.10,
        gbm_kwargs: dict | None = None,
        hmm_kwargs: dict | None = None,
    ) -> None:
        self.w_hmm = w_hmm
        self.w_gbm = w_gbm
        self.w_tsfm = w_tsfm
        self.transition_soften = transition_soften
        self.hmm = KostolanyHMM(**(hmm_kwargs or {}))
        gbm_options = {"cycle_smooth": 0.0}
        gbm_options.update(gbm_kwargs or {})
        self.gbm = KostolanyGBM(**gbm_options)
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
        blend = causal_cycle_filter(blend, strength=0.18)
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
        context = pd.concat([X_train.tail(252), X_test])
        pred = self.predict(context)
        return pred.regimes.reindex(X_test.index).ffill().bfill(), pred.proba.reindex(X_test.index)
