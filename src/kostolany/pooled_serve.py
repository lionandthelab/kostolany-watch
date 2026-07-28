"""Production serving for the promoted pooled 63-day forecaster.

Fits once across a multi-asset panel, persists to disk/GCS, and answers
per-symbol h63 median / quantile / P(up) for Flows.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any

import joblib
import pandas as pd

from kostolany.blob_cache import pull_if_missing, push_async
from kostolany.engine import prepare_xy
from kostolany.harness.pooled_forecast import (
    DEFAULT_PANEL,
    PooledDirectModel,
    _tabular_design,
    load_panel,
)
from kostolany.settings import get_settings

log = logging.getLogger(__name__)

# Panel that cleared equity promotion screens (KS11 68% / S&P 77%).
SERVING_PANEL: tuple[str, ...] = (
    "KS11",
    "^GSPC",
    "BTC-USD",
    "SPY",
    "GLD",
    "TLT",
    "XLE",
)
POOLED_TTL_HOURS = 24.0
_MODEL_NAME = "pooled_direct_v1.joblib"

_lock = threading.Lock()
_model: PooledDirectModel | None = None
_fitted_at: float | None = None
_fitting = False


def _model_path() -> Path:
    return get_settings().cache_path / _MODEL_NAME


def _fresh() -> bool:
    if _model is None or _fitted_at is None:
        return False
    return (time.time() - _fitted_at) < POOLED_TTL_HOURS * 3600.0


def _load_disk() -> PooledDirectModel | None:
    path = _model_path()
    pull_if_missing(path)
    if not path.exists():
        return None
    age_h = (time.time() - path.stat().st_mtime) / 3600.0
    if age_h > POOLED_TTL_HOURS:
        return None
    try:
        blob = joblib.load(path)
        model = blob.get("model")
        if isinstance(model, PooledDirectModel) and model.median_ is not None:
            return model
    except Exception as exc:  # noqa: BLE001
        log.warning("pooled disk load failed: %s", exc)
    return None


def _save_disk(model: PooledDirectModel) -> None:
    path = _model_path()
    try:
        joblib.dump({"model": model, "panel": list(SERVING_PANEL), "saved_at": time.time()}, path)
        push_async(path)
    except Exception as exc:  # noqa: BLE001
        log.warning("pooled disk save failed: %s", exc)


def ensure_pooled_model(*, force: bool = False) -> PooledDirectModel:
    """Return a fitted pooled model; refit if stale/missing."""
    global _model, _fitted_at, _fitting
    with _lock:
        if not force and _fresh() and _model is not None:
            return _model
        if not force:
            disk = _load_disk()
            if disk is not None:
                _model = disk
                _fitted_at = time.time()
                return _model
        # Wait for an in-flight fit instead of starting a second one.
        while _fitting:
            _lock.release()
            time.sleep(0.4)
            _lock.acquire()
            if not force and _fresh() and _model is not None:
                return _model
        _fitting = True

    try:
        log.info("Fitting pooled model on panel %s", SERVING_PANEL)
        panel = load_panel(SERVING_PANEL, start="2010-01-01")
        cutoff = min(pd.Timestamp(design.index[-1]) for design, _ in panel.values())
        model = PooledDirectModel(n_estimators=220).fit(panel, cutoff)
        _save_disk(model)
        with _lock:
            _model = model
            _fitted_at = time.time()
            return model
    finally:
        with _lock:
            _fitting = False


def forecast_symbol(symbol: str) -> dict[str, float]:
    """Causal h63 forecast for one symbol using the pooled model."""
    from kostolany.connectors import load_market

    model = ensure_pooled_model(force=False)
    market = load_market(symbol, start="2010-01-01", enrich_fred=False)
    X, _y_weak, _y_gold, _prices = prepare_xy(market)
    design = _tabular_design(X.dropna(), symbol).dropna()
    if design.empty:
        raise ValueError(f"No design rows for {symbol}")
    median, low, high, p_up = model.predict(design.iloc[[-1]])
    return {
        "h63": float(median),
        "q10": float(low),
        "q90": float(high),
        "p_up": float(p_up),
    }


def warmup_pooled(*, force: bool = False) -> dict[str, Any]:
    """Background-friendly kick used by API lifespan / flows warmup."""
    try:
        model = ensure_pooled_model(force=force)
        return {
            "ok": True,
            "columns": len(model.columns_),
            "panel": list(SERVING_PANEL),
            "default_panel_size": len(DEFAULT_PANEL),
        }
    except Exception as exc:  # noqa: BLE001
        log.exception("pooled warmup failed")
        return {"ok": False, "error": str(exc)[:200]}
