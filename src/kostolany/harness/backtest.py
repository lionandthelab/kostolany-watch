"""Economic backtest of Kostolany action recommendations with execution lag."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from math import erf, sqrt

import numpy as np
import pandas as pd

from kostolany.regimes import Regime


# Position target by regime: +1 long, 0 flat, -1 short (short optional)
DEFAULT_POSITION = {
    Regime.A1: 1.0,
    Regime.A2: 0.5,
    Regime.A3: 0.0,
    Regime.B1: 0.0,
    Regime.B2: 0.0,
    Regime.B3: 1.0,
}


@dataclass
class BacktestConfig:
    execution_lag: int = 1
    cost_bps: float = 5.0  # one-way transaction cost in basis points
    allow_short: bool = False
    position_map: dict[int, float] | None = None


@dataclass
class BacktestResult:
    total_return: float
    cagr: float
    sharpe: float
    max_drawdown: float
    hit_rate: float
    turnover: float
    n_days: int
    equity_curve: pd.Series
    benchmark_return: float
    excess_return: float
    probabilistic_sharpe: float = float("nan")
    deflated_sharpe: float = float("nan")

    def to_dict(self) -> dict:
        d = asdict(self)
        d["equity_curve"] = None  # keep JSON light; caller can keep series
        return d


def _max_drawdown(equity: pd.Series) -> float:
    peak = equity.cummax()
    dd = equity / peak - 1.0
    return float(dd.min())


def probabilistic_sharpe_ratio(
    sharpe: float,
    n: int,
    *,
    skew: float = 0.0,
    kurt: float = 3.0,
    sr_benchmark: float = 0.0,
) -> float:
    """PSR (Bailey & López de Prado): P(true SR > sr_benchmark)."""
    if n < 10 or not np.isfinite(sharpe):
        return float("nan")
    numer = (sharpe - sr_benchmark) * sqrt(n - 1)
    denom = sqrt(max(1e-12, 1.0 - skew * sharpe + ((kurt - 1) / 4.0) * sharpe**2))
    z = numer / denom
    return float(0.5 * (1.0 + erf(z / sqrt(2.0))))


def norm_ppf(p: float) -> float:
    """Approximate inverse CDF of standard normal (Acklam)."""
    a = [
        -3.969683028665376e01,
        2.209460984245205e02,
        -2.759285104469687e02,
        1.383577518672690e02,
        -3.066479806614716e01,
        2.506628277459239e00,
    ]
    b = [
        -5.447609879822406e01,
        1.615858368580409e02,
        -1.556989798598866e02,
        6.680131188771972e01,
        -1.328068155288572e01,
    ]
    c = [
        -7.784894002430293e-03,
        -3.223964580411365e-01,
        -2.400758277161838e00,
        -2.549732539343734e00,
        4.374664141464968e00,
        2.938163982698783e00,
    ]
    d = [
        7.784695709041462e-03,
        3.224671290700398e-01,
        2.445134137142996e00,
        3.754408661907416e00,
    ]
    p = float(np.clip(p, 1e-12, 1 - 1e-12))
    if p < 0.02425:
        q = sqrt(-2 * np.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
            (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1
        )
    if p > 1 - 0.02425:
        q = sqrt(-2 * np.log(1 - p))
        return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
            (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1
        )
    q = p - 0.5
    r = q * q
    return (
        (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5])
        * q
        / (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)
    )


def deflated_sharpe_ratio(
    sharpe: float,
    n: int,
    n_trials: int,
    *,
    skew: float = 0.0,
    kurt: float = 3.0,
) -> float:
    """DSR: PSR against the expected max SR under n_trials independent tests."""
    if n_trials < 1 or n < 10:
        return float("nan")
    e_max = (1 - np.euler_gamma) * norm_ppf(1 - 1 / n_trials) + np.euler_gamma * norm_ppf(
        1 - 1 / (n_trials * np.e)
    )
    sr_0 = float(e_max / sqrt(n))
    return probabilistic_sharpe_ratio(sharpe, n, skew=skew, kurt=kurt, sr_benchmark=sr_0)


def economic_backtest(
    prices: pd.Series,
    regimes: pd.Series,
    config: BacktestConfig | None = None,
    *,
    n_trials: int = 1,
) -> BacktestResult:
    """Apply regime→position map with lag and costs; compare to buy&hold."""
    cfg = config or BacktestConfig()
    pos_map = cfg.position_map or {int(k): v for k, v in DEFAULT_POSITION.items()}

    px = prices.astype(float).sort_index()
    rets = px.pct_change().fillna(0.0)
    signal = regimes.reindex(px.index).ffill()
    target = signal.map(lambda r: float(pos_map.get(int(r), 0.0)) if pd.notna(r) else 0.0)

    if not cfg.allow_short:
        target = target.clip(lower=0.0)

    position = target.shift(cfg.execution_lag).fillna(0.0)
    turnover = position.diff().abs().fillna(0.0)
    costs = turnover * (cfg.cost_bps / 1e4)
    strat_rets = position * rets - costs
    equity = (1.0 + strat_rets).cumprod()
    bh = (1.0 + rets).cumprod()

    n = len(equity)
    years = max(n / 252.0, 1e-9)
    total = float(equity.iloc[-1] - 1.0)
    cagr = float(equity.iloc[-1] ** (1 / years) - 1.0) if equity.iloc[-1] > 0 else -1.0
    vol = float(strat_rets.std() * np.sqrt(252))
    sharpe = float(strat_rets.mean() * 252 / vol) if vol > 1e-12 else 0.0
    hit = float((strat_rets[position != 0] > 0).mean()) if (position != 0).any() else float("nan")
    skew = float(strat_rets.skew()) if n > 3 else 0.0
    kurt = float(strat_rets.kurtosis() + 3.0) if n > 3 else 3.0
    psr = probabilistic_sharpe_ratio(sharpe, n, skew=skew, kurt=kurt)
    dsr = deflated_sharpe_ratio(sharpe, n, n_trials=n_trials, skew=skew, kurt=kurt)

    return BacktestResult(
        total_return=total,
        cagr=cagr,
        sharpe=sharpe,
        max_drawdown=_max_drawdown(equity),
        hit_rate=hit,
        turnover=float(turnover.mean() * 252),
        n_days=n,
        equity_curve=equity,
        benchmark_return=float(bh.iloc[-1] - 1.0),
        excess_return=total - float(bh.iloc[-1] - 1.0),
        probabilistic_sharpe=psr,
        deflated_sharpe=dsr,
    )
