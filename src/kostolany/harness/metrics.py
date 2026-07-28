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


@dataclass
class TransitionEval:
    n_true_transitions: int
    n_detected: int
    mean_lead_days: float  # positive = early, negative = late
    hit_within_window: float
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

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


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

    return TransitionEval(
        n_true_transitions=len(true_pos),
        n_detected=hits,
        mean_lead_days=float(np.mean(leads)) if leads else float("nan"),
        hit_within_window=float(hits / len(true_pos)) if len(true_pos) else float("nan"),
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
        adjacent_accuracy=float(
            np.mean(np.minimum(np.abs(yt - yp), 6 - np.abs(yt - yp)) <= 1)
        ),
        mean_cycle_distance=float(
            np.mean(np.minimum(np.abs(yt - yp), 6 - np.abs(yt - yp)))
        ),
        macro_f1=float(f1_score(yt, yp, average="macro", labels=labels, zero_division=0)),
        weighted_f1=float(f1_score(yt, yp, average="weighted", labels=labels, zero_division=0)),
        per_class_f1=per_f1,
        confusion=cm,
        classification_report=report,
        transition=evaluate_transitions(y_true, y_pred, window=transition_window),
        calibration=cal,
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
