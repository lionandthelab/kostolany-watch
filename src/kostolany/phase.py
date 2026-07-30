"""Continuous phase head — sin/cos ridge on the CAUSAL weak-label angle.

Why an angle instead of a 6-way class: the 6-way regime target factorises into
SIDE (up-leg vs down-leg — learnable) and THIRD (which third of the leg — not
learnable; the natural causal proxy correlates ~0.08 with truth and tertile
accuracy sits at chance). Exact-6 accuracy is therefore capped near
``side_accuracy / 3``, which is arithmetic, not a modelling gap. Regressing a
phase *angle* spends all model capacity on the learnable degree of freedom and
lets the circle geometry absorb the rest: when the third is uncertain the
estimate relaxes toward the middle of the leg, so the predicted sector lands
*adjacent* rather than opposite.

NON-NEGOTIABLE: the training target is always the causal ``weak_labels`` angle.
``gold_phase_angle`` / ``gold_leg_segments`` below are EVAL ONLY and are never
reachable from :meth:`PhaseHead.fit`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.stats import vonmises
from sklearn.linear_model import Ridge

from kostolany.features import FEATURE_SPECS

TWO_PI = 2.0 * math.pi
SECTOR_WIDTH = math.pi / 3.0
N_SECTORS = 6

# Compact causal subset of ``model_matrix`` (23 columns). Deliberately small:
# ~35-42 independent legs per market support ~12 parameters.
DEFAULT_PHASE_FEATURES: tuple[str, ...] = (
    "ret_5",
    "ret_10",
    "ret_20",
    "ret_60",
    "trend_slope",
    "trend_accel",
    "ma_gap",
    "drawdown",
    "range_pos_252",
    "rv_10",
    "rv_60",
    "vol_z",
)

_FEATURE_GROUP = {spec.name: spec.group for spec in FEATURE_SPECS}


def wrap_pi(angle: np.ndarray | float) -> np.ndarray | float:
    """Wrap an angle (or array of angles) into [-pi, pi)."""
    return (np.asarray(angle, dtype=float) + math.pi) % TWO_PI - math.pi


def wrap_two_pi(angle: np.ndarray | float) -> np.ndarray | float:
    """Wrap an angle (or array of angles) into [0, 2*pi)."""
    return np.asarray(angle, dtype=float) % TWO_PI


def weak_phase_angle(y_weak: pd.Series) -> pd.Series:
    """Map causal weak labels (class 0..5) to their sector-centre angle.

    theta_weak = (c + 0.5) * (pi / 3). This is the ONLY training target the
    phase head ever sees; ``weak_labels`` is rule-based and point-in-time.
    """
    return ((y_weak.astype(float) + 0.5) * SECTOR_WIDTH).rename("theta_weak")


def angle_to_sector(theta: np.ndarray | pd.Series) -> np.ndarray:
    """Sector index 0..5 of an angle — for colour and naming only."""
    wrapped = wrap_two_pi(np.asarray(theta, dtype=float))
    return np.clip((wrapped // SECTOR_WIDTH).astype(int), 0, N_SECTORS - 1)


def mardia_kappa(
    residuals: np.ndarray,
    *,
    bounds: tuple[float, float] = (0.05, 12.0),
) -> float:
    """Von Mises concentration from angular residuals (Mardia mean-resultant-length).

    Bounded away from 0 and from very large values: kappa -> 0 gives a useless
    uniform posterior, kappa very large underflows the far-sector mass to
    exactly zero, which is the structurally-zero-class bug we are avoiding.
    """
    r = np.asarray(residuals, dtype=float)
    r = r[np.isfinite(r)]
    lo, hi = bounds
    if r.size < 2:
        return float(lo)
    rbar = float(np.hypot(np.mean(np.cos(r)), np.mean(np.sin(r))))
    rbar = min(max(rbar, 0.0), 1.0 - 1e-9)
    if rbar < 0.53:
        kappa = 2.0 * rbar + rbar**3 + 5.0 * rbar**5 / 6.0
    elif rbar < 0.85:
        kappa = -0.4 + 1.39 * rbar + 0.43 / (1.0 - rbar)
    else:
        kappa = 1.0 / (rbar**3 - 4.0 * rbar**2 + 3.0 * rbar)
    if not np.isfinite(kappa):
        return float(hi)
    return float(min(max(kappa, lo), hi))


def sector_probabilities(
    theta: np.ndarray,
    kappa: float,
    *,
    floor: float = 1e-9,
) -> np.ndarray:
    """Integrate a von Mises density centred at theta over each 60-degree sector.

    Uses the wrapped CDF over the six sector edges so the arc that straddles the
    branch cut is handled correctly; the six arcs tile the circle so the masses
    sum to one by construction.
    """
    th = np.asarray(theta, dtype=float).reshape(-1, 1)
    edges = np.arange(N_SECTORS + 1, dtype=float) * SECTOR_WIDTH
    delta = wrap_pi(edges.reshape(1, -1) - th)
    cdf = vonmises.cdf(delta, kappa)
    # A negative difference means the arc crossed the branch cut; mod 1 restores
    # the counter-clockwise mass.
    mass = np.diff(cdf, axis=1) % 1.0
    mass = np.clip(mass, floor, None)
    return mass / mass.sum(axis=1, keepdims=True)


@dataclass
class PhasePrediction:
    """Row-wise phase estimate. ``proba`` columns are p0..p5."""

    theta: pd.Series
    R: pd.Series
    kappa: float
    proba: pd.DataFrame
    regimes: pd.Series
    sin_hat: pd.Series | None = None
    cos_hat: pd.Series | None = None


class PhaseHead:
    """Two independent ridges -> (sin theta, cos theta), von Mises calibrated.

    Mirrors the ``fit`` / ``predict`` / ``fit_predict`` surface of
    ``KostolanyHMM`` / ``KostolanyGBM`` so it drops straight into the existing
    walk-forward harness.
    """

    def __init__(
        self,
        *,
        alpha: float = 10.0,
        features: tuple[str, ...] = DEFAULT_PHASE_FEATURES,
        inner_holdout: int = 252,
        min_inner_prefix: int = 252,
        kappa_bounds: tuple[float, float] = (0.05, 12.0),
        refit_on_full: bool = True,
        prob_floor: float = 1e-9,
    ) -> None:
        self.alpha = float(alpha)
        self.features = tuple(features)
        self.inner_holdout = int(inner_holdout)
        self.min_inner_prefix = int(min_inner_prefix)
        self.kappa_bounds = kappa_bounds
        self.refit_on_full = bool(refit_on_full)
        self.prob_floor = float(prob_floor)

        self.columns_: list[str] = []
        self.coef_sin_: np.ndarray | None = None
        self.coef_cos_: np.ndarray | None = None
        self.intercept_sin_: float = 0.0
        self.intercept_cos_: float = 0.0
        self.kappa_: float = float(kappa_bounds[0])
        self.n_calibration_: int = 0
        self.calibration_in_sample_: bool = False
        self.n_train_: int = 0

    # ---------------------------------------------------------------- design

    def _select_columns(self, X: pd.DataFrame) -> list[str]:
        return [c for c in self.features if c in X.columns]

    def _design(self, X: pd.DataFrame) -> pd.DataFrame:
        missing = [c for c in self.columns_ if c not in X.columns]
        if missing:
            raise KeyError(f"PhaseHead is missing fitted columns: {missing}")
        # model_matrix already applies a causal rolling standardisation, so 0.0
        # is the point-in-time mean — a neutral fill for warm-up NaNs.
        return X[self.columns_].astype(float).fillna(0.0)

    # ------------------------------------------------------------------- fit

    def _fit_pair(self, design: np.ndarray, theta: np.ndarray) -> tuple[Ridge, Ridge]:
        ridge_sin = Ridge(alpha=self.alpha, fit_intercept=True)
        ridge_cos = Ridge(alpha=self.alpha, fit_intercept=True)
        ridge_sin.fit(design, np.sin(theta))
        ridge_cos.fit(design, np.cos(theta))
        return ridge_sin, ridge_cos

    def fit(self, X: pd.DataFrame, y_weak: pd.Series) -> "PhaseHead":
        """Fit on the causal weak-label angle. Gold never reaches this method."""
        self.columns_ = self._select_columns(X)
        if not self.columns_:
            raise ValueError("None of the PhaseHead features are present in X")

        theta = weak_phase_angle(y_weak)
        frame = X[self.columns_].astype(float).join(theta, how="inner").dropna()
        if len(frame) < 60:
            raise ValueError(f"Not enough clean rows to fit PhaseHead: {len(frame)}")

        design = frame[self.columns_].to_numpy(dtype=float)
        target = frame["theta_weak"].to_numpy(dtype=float)
        self.n_train_ = len(frame)

        # Inner holdout: the trailing slice of the TRAINING window, which the
        # calibration ridge is not fitted on. kappa must not be read off the
        # ridge's own residuals or the posterior comes out overconfident.
        hold = self.inner_holdout
        if len(frame) - hold < self.min_inner_prefix:
            hold = max(0, len(frame) - self.min_inner_prefix)
        if hold >= 30:
            cut = len(frame) - hold
            inner_sin, inner_cos = self._fit_pair(design[:cut], target[:cut])
            s_hat = inner_sin.predict(design[cut:])
            c_hat = inner_cos.predict(design[cut:])
            residual = wrap_pi(target[cut:] - np.arctan2(s_hat, c_hat))
            self.calibration_in_sample_ = False
        else:
            # Degenerate short slice: fall back to in-sample residuals and say so.
            inner_sin, inner_cos = self._fit_pair(design, target)
            residual = wrap_pi(
                target - np.arctan2(inner_sin.predict(design), inner_cos.predict(design))
            )
            self.calibration_in_sample_ = True
        self.kappa_ = mardia_kappa(residual, bounds=self.kappa_bounds)
        self.n_calibration_ = int(np.size(residual))

        if self.refit_on_full:
            # Serving ridge uses the whole training slice so the most recent
            # bars are not discarded; kappa stays the out-of-fold (conservative)
            # estimate above.
            ridge_sin, ridge_cos = self._fit_pair(design, target)
        else:
            ridge_sin, ridge_cos = inner_sin, inner_cos

        self.coef_sin_ = np.asarray(ridge_sin.coef_, dtype=float).ravel()
        self.coef_cos_ = np.asarray(ridge_cos.coef_, dtype=float).ravel()
        self.intercept_sin_ = float(ridge_sin.intercept_)
        self.intercept_cos_ = float(ridge_cos.intercept_)
        return self

    # --------------------------------------------------------------- predict

    def _raw(self, X: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
        if self.coef_sin_ is None or self.coef_cos_ is None:
            raise RuntimeError("PhaseHead is not fitted")
        design = self._design(X)
        arr = design.to_numpy(dtype=float)
        s = pd.Series(arr @ self.coef_sin_ + self.intercept_sin_, index=design.index)
        c = pd.Series(arr @ self.coef_cos_ + self.intercept_cos_, index=design.index)
        return s, c

    def predict(self, X: pd.DataFrame) -> PhasePrediction:
        s, c = self._raw(X)
        theta = pd.Series(
            wrap_two_pi(np.arctan2(s.to_numpy(), c.to_numpy())), index=s.index, name="theta"
        )
        radius = pd.Series(np.hypot(s.to_numpy(), c.to_numpy()), index=s.index, name="R")
        proba = pd.DataFrame(
            sector_probabilities(theta.to_numpy(), self.kappa_, floor=self.prob_floor),
            index=s.index,
            columns=[f"p{i}" for i in range(N_SECTORS)],
        )
        regimes = pd.Series(angle_to_sector(theta), index=s.index, name="regime")
        return PhasePrediction(
            theta=theta,
            R=radius,
            kappa=float(self.kappa_),
            proba=proba,
            regimes=regimes,
            sin_hat=s.rename("sin_hat"),
            cos_hat=c.rename("cos_hat"),
        )

    def fit_predict(
        self, X_train: pd.DataFrame, y_train: pd.Series, X_test: pd.DataFrame
    ) -> tuple[pd.Series, pd.DataFrame]:
        self.fit(X_train, y_train)
        pred = self.predict(X_test)
        return pred.regimes.reindex(X_test.index), pred.proba.reindex(X_test.index)

    # ----------------------------------------------------------- attribution

    def attribution(self, X_row: pd.Series | pd.DataFrame) -> dict:
        """Exact additive decomposition of theta_hat into per-feature angles.

        theta = atan2(s, c) is not linear, so contributions are taken as the
        telescoping angular displacement from adding each feature to the running
        (sin, cos) vector in the fitted column order. The wrapped increments
        telescope exactly, so ``sum(contributions) == wrap(theta_hat - theta_0)``
        holds to float tolerance by construction. Order dependence is the usual
        caveat of a path decomposition; the order is the fixed feature order.
        """
        if self.coef_sin_ is None or self.coef_cos_ is None:
            raise RuntimeError("PhaseHead is not fitted")
        if isinstance(X_row, pd.DataFrame):
            if len(X_row) != 1:
                raise ValueError("attribution expects exactly one row")
            row = X_row.iloc[0]
        else:
            row = X_row
        values = np.asarray(
            [float(row.get(c, 0.0)) if pd.notna(row.get(c, 0.0)) else 0.0 for c in self.columns_],
            dtype=float,
        )

        s = self.intercept_sin_
        c = self.intercept_cos_
        theta_intercept = math.atan2(s, c)
        previous = theta_intercept
        contributions: dict[str, float] = {}
        for j, name in enumerate(self.columns_):
            s += float(self.coef_sin_[j]) * values[j]
            c += float(self.coef_cos_[j]) * values[j]
            current = math.atan2(s, c)
            contributions[name] = float(wrap_pi(current - previous))
            previous = current

        groups: dict[str, float] = {}
        for name, value in contributions.items():
            group = _FEATURE_GROUP.get(name, "position")
            groups[group] = groups.get(group, 0.0) + value

        theta_hat = previous
        return {
            "theta": float(wrap_two_pi(theta_hat)),
            "theta_intercept": float(wrap_two_pi(theta_intercept)),
            "delta": float(sum(contributions.values())),
            "R": float(math.hypot(s, c)),
            "kappa": float(self.kappa_),
            "features": contributions,
            "groups": groups,
        }


# --------------------------------------------------------------------------
# EVAL ONLY below this line
# --------------------------------------------------------------------------


def gold_leg_segments(prices: pd.Series, *, min_cycle: int = 60) -> pd.DataFrame:
    """Per-bar leg id / side / within-leg position u. EVAL ONLY.

    The legs are read back off ``labels.gold_labels``' own output rather than
    re-deriving the turning points here. Two reasons: (1) it makes the
    continuous gold angle the *same object* as the discrete gold label by
    construction, and (2) ``labels.py`` owns that segmentation and has already
    changed its minimum-gap rule once (calendar days -> trading bars) — a
    private copy here would silently drift out of sync.

    A leg is a maximal run on one side of the egg (A1/A2/A3 = up, B1/B2/B3 =
    down); gold legs strictly alternate, so a side change is a leg boundary.
    The leg id is the unit a bootstrap must resample: bars inside one leg are
    not independent.
    """
    from kostolany.labels import gold_labels  # EVAL ONLY; never reached from fit()

    labels = gold_labels(prices.astype(float).sort_index(), min_cycle=min_cycle)
    arr = labels.to_numpy(dtype=int)
    side = np.where(arr >= 3, -1, 1)
    boundary = np.concatenate([[True], side[1:] != side[:-1]])
    starts = np.flatnonzero(boundary)
    lengths = np.diff(np.concatenate([starts, [len(side)]]))
    within = np.arange(len(side)) - np.repeat(starts, lengths)
    leg_len = np.repeat(lengths, lengths)
    return pd.DataFrame(
        {
            "leg_id": np.repeat(np.arange(len(starts)), lengths),
            "side": side,
            "u": within / leg_len.astype(float),
        },
        index=labels.index,
    )


def gold_phase_angle(prices: pd.Series, *, min_cycle: int = 60) -> pd.Series:
    """Continuous gold phase angle in [0, 2*pi). EVAL ONLY — never a fit target.

    Uses the same post-hoc leg segmentation as ``labels.gold_labels``: for the
    k-th bar of a leg of length L, u = k / L; an up-leg maps to theta = pi * u
    and a down-leg to theta = pi + pi * u. This uses FUTURE INFORMATION by
    construction (the turning points are only knowable after the fact), so it
    exists purely to score causal estimators.
    """
    segments = gold_leg_segments(prices, min_cycle=min_cycle)
    u = segments["u"].to_numpy(dtype=float)
    side = segments["side"].to_numpy(dtype=int)
    theta = np.where(side > 0, math.pi * u, math.pi + math.pi * u)
    return pd.Series(theta, index=segments.index, name="theta_gold")


def gold_phase_sectors(prices: pd.Series, *, min_cycle: int = 60) -> pd.Series:
    """Sector 0..5 of the continuous gold angle. EVAL ONLY."""
    theta = gold_phase_angle(prices, min_cycle=min_cycle)
    return pd.Series(angle_to_sector(theta), index=theta.index, name="gold_sector")
