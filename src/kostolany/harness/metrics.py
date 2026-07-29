"""Three-layer regime evaluation metrics + probability calibration."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Mapping

import numpy as np
import pandas as pd
from sklearn.metrics import (
    accuracy_score,
    brier_score_loss,
    classification_report,
    confusion_matrix,
    f1_score,
    log_loss,
)

from kostolany.regimes import REGIME_META, REGIME_ORDER, Regime


N_REGIMES = len(REGIME_ORDER)


@dataclass
class TransitionEval:
    n_true_transitions: int
    n_detected: int
    mean_lead_days: float  # positive = early, negative = late
    hit_within_window: float
    # Recall alone is gameable: a predictor that flips every bar covers every
    # true flip and scores 1.000. The matched fields below are the headline.
    n_pred_transitions: int = 0
    matched_within_window: int = 0
    precision_within_window: float = float("nan")
    recall_within_window: float = float("nan")
    f1_within_window: float = float("nan")
    details: list[dict] = field(default_factory=list)


@dataclass
class CalibrationEval:
    brier: float
    log_loss: float
    ece: float  # expected calibration error (max-prob bins)


@dataclass
class RegimeEvalReport:
    accuracy: float
    adjacent_accuracy: float
    mean_cycle_distance: float
    macro_f1: float
    weighted_f1: float
    per_class_f1: dict[str, float]
    confusion: list[list[int]]
    classification_report: dict
    transition: TransitionEval
    calibration: CalibrationEval
    # Trivial predictors scored on the SAME rows — without them
    # `adjacent_accuracy` reads as skill when its floor is structural.
    baselines: dict[str, float] = field(default_factory=dict)
    quadratic_weighted_kappa: float = float("nan")

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


def _cycle_distance(a: np.ndarray, b: np.ndarray, k: int = N_REGIMES) -> np.ndarray:
    """Distance on the 6-cycle A1..B3 (B3 is adjacent to A1)."""
    d = np.abs(np.asarray(a) - np.asarray(b))
    return np.minimum(d, k - d)


def quadratic_weighted_kappa(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    *,
    n_classes: int = N_REGIMES,
) -> float:
    """QWK using the cyclic distance, i.e. chance-corrected `mean_cycle_distance`.

    Unlike adjacent accuracy (structural floor 0.500 on a 6-cycle) this is 0.0
    for any predictor that is independent of the truth, whatever its marginal.
    """
    yt = np.asarray(y_true, dtype=int)
    yp = np.asarray(y_pred, dtype=int)
    if len(yt) == 0:
        return float("nan")
    idx = np.arange(n_classes)
    weights = _cycle_distance(idx[:, None], idx[None, :], n_classes).astype(float) ** 2
    weights /= weights.max()  # max cyclic distance on a 6-cycle is 3

    observed = confusion_matrix(yt, yp, labels=list(idx)).astype(float)
    observed /= max(1.0, observed.sum())
    hist_true = np.bincount(yt, minlength=n_classes).astype(float) / len(yt)
    hist_pred = np.bincount(yp, minlength=n_classes).astype(float) / len(yp)
    expected = np.outer(hist_true, hist_pred)

    denom = float((weights * expected).sum())
    if denom <= 1e-12:
        return float("nan")
    return float(1.0 - float((weights * observed).sum()) / denom)


def regime_baselines(y_true: np.ndarray, *, n_classes: int = N_REGIMES) -> dict[str, float]:
    """Battery of trivial predictors scored on the evaluation rows themselves.

    These are the numbers that decide whether a headline metric is skill: the
    6-cycle gives adjacent accuracy a ~0.500 floor, and a constant class-prior
    predictor scores Brier ``(1 - sum p_k^2) / 6`` with ECE exactly 0.
    """
    yt = np.asarray(y_true, dtype=int)
    n = len(yt)
    if n == 0:
        return {}
    freq = np.bincount(yt, minlength=n_classes).astype(float) / n

    const_adjacent: list[float] = []
    const_cycdist: list[float] = []
    for c in range(n_classes):
        d = _cycle_distance(yt, np.full(n, c), n_classes)
        const_adjacent.append(float(np.mean(d <= 1)))
        const_cycdist.append(float(np.mean(d)))

    prior_proba = np.tile(freq, (n, 1))
    uniform_proba = np.full((n, n_classes), 1.0 / n_classes)
    onehot = np.eye(n_classes)[yt]

    out = {
        "n_rows": float(n),
        # Most frequent gold class — the floor for `accuracy`.
        "majority": float(freq.max()),
        # Guessing from the gold marginal: sum of squared class frequencies.
        "marginal_random": float(np.sum(freq**2)),
        "best_constant_adjacent": float(max(const_adjacent)),
        "best_constant_cycdist": float(min(const_cycdist)),
        "constant_prior_brier": float(np.mean(np.mean((onehot - prior_proba) ** 2, axis=0))),
        "constant_prior_ece": float(_ece(yt, prior_proba)),
        "uniform_brier": float(np.mean(np.mean((onehot - uniform_proba) ** 2, axis=0))),
    }
    if n > 1:
        # Yesterday's gold label — the bar any "regime is sticky" claim must clear.
        out["persistence"] = float(np.mean(yt[1:] == yt[:-1]))
    return out


def _ece(y_true: np.ndarray, proba: np.ndarray, n_bins: int = 10) -> float:
    conf = proba.max(axis=1)
    pred = proba.argmax(axis=1)
    correct = (pred == y_true).astype(float)
    bins = np.linspace(0.0, 1.0, n_bins + 1)
    ece = 0.0
    for i in range(n_bins):
        m = (conf > bins[i]) & (conf <= bins[i + 1])
        if not np.any(m):
            continue
        ece += abs(correct[m].mean() - conf[m].mean()) * (m.mean())
    return float(ece)


def _greedy_match(
    true_pos: np.ndarray,
    pred_pos: np.ndarray,
    window: int,
) -> list[tuple[int, int]]:
    """One-to-one nearest-first matching of predicted flips to true flips.

    Each predicted flip may claim at most one true flip and vice versa, so
    spraying flips no longer buys recall for free.
    """
    candidates = sorted(
        (abs(int(p) - int(t)), int(t), int(p))
        for t in true_pos
        for p in pred_pos
        if abs(int(p) - int(t)) <= window
    )
    used_true: set[int] = set()
    used_pred: set[int] = set()
    matches: list[tuple[int, int]] = []
    for _, t, p in candidates:
        if t in used_true or p in used_pred:
            continue
        used_true.add(t)
        used_pred.add(p)
        matches.append((t, p))
    return matches


def evaluate_transitions(
    y_true: pd.Series,
    y_pred: pd.Series,
    *,
    window: int = 10,
) -> TransitionEval:
    """Measure how early/late predicted regime flips match true flips."""
    yt = y_true.dropna().astype(int)
    yp = y_pred.reindex(yt.index).astype(float)

    true_pos = np.flatnonzero((yt.diff().fillna(0) != 0).to_numpy())
    pred_pos = np.flatnonzero(
        (yp.diff().fillna(0) != 0).to_numpy() & yp.notna().to_numpy()
    )

    details: list[dict] = []
    leads: list[float] = []
    hits = 0
    for pos in true_pos:
        t = yt.index[pos]
        # Compare trading-bar positions, not calendar-day gaps.
        deltas = [int(p - pos) for p in pred_pos]
        if not deltas:
            details.append({"true": str(t), "matched": False})
            continue
        best = min(deltas, key=lambda d: abs(d))
        if abs(best) <= window:
            hits += 1
            leads.append(float(-best))  # early => positive lead
            details.append({"true": str(t), "matched": True, "lead_days": float(-best)})
        else:
            details.append({"true": str(t), "matched": False, "nearest_delta": best})

    matches = _greedy_match(true_pos, pred_pos, window)
    n_matched = len(matches)
    precision = float(n_matched / len(pred_pos)) if len(pred_pos) else float("nan")
    recall = float(n_matched / len(true_pos)) if len(true_pos) else float("nan")
    if np.isfinite(precision) and np.isfinite(recall) and (precision + recall) > 0:
        f1 = float(2.0 * precision * recall / (precision + recall))
    else:
        f1 = 0.0 if (len(true_pos) or len(pred_pos)) else float("nan")

    return TransitionEval(
        n_true_transitions=len(true_pos),
        n_detected=hits,
        mean_lead_days=float(np.mean(leads)) if leads else float("nan"),
        hit_within_window=float(hits / len(true_pos)) if len(true_pos) else float("nan"),
        n_pred_transitions=int(len(pred_pos)),
        matched_within_window=int(n_matched),
        precision_within_window=precision,
        recall_within_window=recall,
        f1_within_window=f1,
        details=details[:50],
    )


def evaluate_regimes(
    y_true: pd.Series,
    y_pred: pd.Series,
    proba: pd.DataFrame | None = None,
    *,
    transition_window: int = 10,
) -> RegimeEvalReport:
    aligned = pd.concat([y_true.rename("y"), y_pred.rename("p")], axis=1).dropna()
    yt = aligned["y"].astype(int).to_numpy()
    yp = aligned["p"].astype(int).to_numpy()

    labels = [int(r) for r in REGIME_ORDER]
    names = [r.name for r in REGIME_ORDER]

    cm = confusion_matrix(yt, yp, labels=labels).tolist()
    report = classification_report(
        yt, yp, labels=labels, target_names=names, output_dict=True, zero_division=0
    )
    per_f1 = {n: float(report[n]["f1-score"]) for n in names if n in report}

    if proba is not None:
        pr = proba.reindex(aligned.index).dropna()
        common = aligned.index.intersection(pr.index)
        yt2 = aligned.loc[common, "y"].astype(int).to_numpy()
        P = pr.loc[common].to_numpy()
        # one-vs-rest brier average
        briers = []
        for k in range(P.shape[1]):
            briers.append(brier_score_loss((yt2 == k).astype(int), P[:, k]))
        try:
            ll = float(log_loss(yt2, P, labels=list(range(P.shape[1]))))
        except ValueError:
            ll = float("nan")
        cal = CalibrationEval(
            brier=float(np.mean(briers)),
            log_loss=ll,
            ece=_ece(yt2, P),
        )
    else:
        cal = CalibrationEval(brier=float("nan"), log_loss=float("nan"), ece=float("nan"))

    return RegimeEvalReport(
        accuracy=float(accuracy_score(yt, yp)),
        adjacent_accuracy=float(np.mean(_cycle_distance(yt, yp) <= 1)),
        mean_cycle_distance=float(np.mean(_cycle_distance(yt, yp))),
        macro_f1=float(f1_score(yt, yp, average="macro", labels=labels, zero_division=0)),
        weighted_f1=float(f1_score(yt, yp, average="weighted", labels=labels, zero_division=0)),
        per_class_f1=per_f1,
        confusion=cm,
        classification_report=report,
        transition=evaluate_transitions(y_true, y_pred, window=transition_window),
        calibration=cal,
        baselines=regime_baselines(yt),
        quadratic_weighted_kappa=quadratic_weighted_kappa(yt, yp),
    )


def egg_coordinate(proba: Mapping | pd.Series) -> tuple[float, float]:
    """Expected (x, y) on the egg from a probability distribution."""
    if isinstance(proba, pd.Series):
        items = proba.items()
    else:
        items = proba.items()
    p: dict[Regime, float] = {}
    for k, v in items:
        if isinstance(k, Regime):
            p[k] = float(v)
        elif isinstance(k, str) and k in Regime.__members__:
            p[Regime[k]] = float(v)
        elif isinstance(k, str) and k.startswith("p") and k[1:].isdigit():
            p[Regime(int(k[1:]))] = float(v)
        else:
            p[Regime(int(k))] = float(v)
    sx = sy = mass = 0.0
    for r, meta in REGIME_META.items():
        w = float(p.get(r, 0.0))
        sx += w * meta.egg_x
        sy += w * meta.egg_y
        mass += w
    if mass <= 0:
        return 0.0, 0.0
    return sx / mass, sy / mass
