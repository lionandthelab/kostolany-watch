"""SOTA evaluation harness: purged CV, leakage audit, metrics, economic backtest."""

from kostolany.harness.backtest import BacktestConfig, economic_backtest
from kostolany.harness.cv import CombinatorialPurgedCV, PurgedWalkForward
from kostolany.harness.leakage import LeakageAuditor, LeakageReport
from kostolany.harness.metrics import RegimeEvalReport, evaluate_regimes
from kostolany.harness.runner import ExperimentConfig, ExperimentRunner, ExperimentResult

__all__ = [
    "BacktestConfig",
    "CombinatorialPurgedCV",
    "PurgedWalkForward",
    "LeakageAuditor",
    "LeakageReport",
    "RegimeEvalReport",
    "evaluate_regimes",
    "ExperimentConfig",
    "ExperimentRunner",
    "ExperimentResult",
    "economic_backtest",
]
