"""Look-ahead / leakage auditors for the Kostolany pipeline.

These checks are first-class: a model that scores well but fails the auditor
must not be promoted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

import numpy as np
import pandas as pd


@dataclass
class LeakFinding:
    code: str
    severity: str  # "critical" | "warning" | "info"
    message: str
    detail: dict = field(default_factory=dict)


@dataclass
class LeakageReport:
    findings: list[LeakFinding]
    passed: bool

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "findings": [
                {"code": f.code, "severity": f.severity, "message": f.message, "detail": f.detail}
                for f in self.findings
            ],
        }


class LeakageAuditor:
    """Static + statistical leakage checks."""

    def __init__(self, max_abs_corr_with_future: float = 0.35) -> None:
        self.max_abs_corr_with_future = max_abs_corr_with_future

    def audit(
        self,
        features: pd.DataFrame,
        labels: pd.Series,
        *,
        gold_labels: pd.Series | None = None,
        gold_used_in_training: bool = False,
        returns: pd.Series | None = None,
        execution_lag: int = 1,
        feature_shift_check_cols: Iterable[str] | None = None,
    ) -> LeakageReport:
        findings: list[LeakFinding] = []

        # --- Critical: gold labels must never train ---
        if gold_used_in_training:
            findings.append(
                LeakFinding(
                    code="GOLD_IN_TRAIN",
                    severity="critical",
                    message="Gold (post-hoc) labels were used for training — forbidden.",
                )
            )

        # --- Index alignment ---
        if not features.index.equals(labels.index):
            # allow subset if labels are aligned subset
            if not labels.index.isin(features.index).all():
                findings.append(
                    LeakFinding(
                        code="INDEX_MISALIGN",
                        severity="critical",
                        message="Label index contains timestamps absent from features.",
                    )
                )

        # --- NaN burst at start is expected; mid-sample all-NaN columns are not ---
        for col in features.columns:
            s = features[col]
            if s.isna().all():
                findings.append(
                    LeakFinding(
                        code="DEAD_FEATURE",
                        severity="warning",
                        message=f"Feature '{col}' is entirely NaN.",
                        detail={"column": col},
                    )
                )

        # --- Future-return correlation smoke test ---
        # A feature that correlates more with future returns than contemporaneous
        # returns is a classic look-ahead smell (not proof, but a red flag).
        if returns is not None:
            fut = returns.shift(-execution_lag)
            cols = list(feature_shift_check_cols) if feature_shift_check_cols else list(features.columns)
            for col in cols:
                x = features[col]
                c_now = self._safe_corr(x, returns)
                c_fut = self._safe_corr(x, fut)
                if (
                    np.isfinite(c_fut)
                    and abs(c_fut) > self.max_abs_corr_with_future
                    and abs(c_fut) > abs(c_now) + 0.15
                ):
                    findings.append(
                        LeakFinding(
                            code="FUTURE_CORR",
                            severity="warning",
                            message=(
                                f"Feature '{col}' correlates more with future returns "
                                f"(r={c_fut:.2f}) than contemporaneous (r={c_now:.2f})."
                            ),
                            detail={"column": col, "corr_now": c_now, "corr_future": c_fut},
                        )
                    )

        # --- Label lead check: if labels predict past features too well via shift ---
        # Gold labels are built with future peaks — they SHOULD correlate with future
        # price path. That is fine for eval, but we flag if someone feeds them as y_train.
        if gold_labels is not None and labels is not None:
            overlap = labels.dropna().index.intersection(gold_labels.dropna().index)
            if len(overlap) > 50:
                agree = float((labels.loc[overlap] == gold_labels.loc[overlap]).mean())
                if agree > 0.98:
                    findings.append(
                        LeakFinding(
                            code="LABEL_EQUALS_GOLD",
                            severity="warning",
                            message=(
                                "Training/weak labels match gold labels >98%. "
                                "Possible accidental gold leakage into training labels."
                            ),
                            detail={"agreement": agree},
                        )
                    )

        # --- Execution lag must be >= 1 for live-like backtests ---
        if execution_lag < 1:
            findings.append(
                LeakFinding(
                    code="ZERO_LAG",
                    severity="critical",
                    message="execution_lag < 1 allows same-bar trading on the signal bar.",
                )
            )

        critical = any(f.severity == "critical" for f in findings)
        return LeakageReport(findings=findings, passed=not critical)

    @staticmethod
    def _safe_corr(a: pd.Series, b: pd.Series) -> float:
        aligned = pd.concat([a, b], axis=1).dropna()
        if len(aligned) < 30:
            return float("nan")
        return float(aligned.iloc[:, 0].corr(aligned.iloc[:, 1]))
