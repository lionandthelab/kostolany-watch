"""Simple parquet/csv disk cache for connector responses."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from kostolany.settings import get_settings


def cache_path(key: str, suffix: str = "parquet") -> Path:
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in key)
    return get_settings().cache_path / f"{safe}.{suffix}"


def read_cache(key: str, max_age_hours: float | None = 24.0) -> pd.DataFrame | None:
    path = cache_path(key)
    if not path.exists():
        # try csv fallback
        csv = cache_path(key, "csv")
        if not csv.exists():
            return None
        path = csv
    if max_age_hours is not None:
        import time

        age_h = (time.time() - path.stat().st_mtime) / 3600.0
        if age_h > max_age_hours:
            return None
    if path.suffix == ".csv":
        df = pd.read_csv(path, index_col=0, parse_dates=True)
    else:
        df = pd.read_parquet(path)
    df.index = pd.to_datetime(df.index)
    return df


def write_cache(key: str, df: pd.DataFrame) -> Path:
    path = cache_path(key)
    try:
        df.to_parquet(path)
        return path
    except Exception:  # noqa: BLE001
        csv = cache_path(key, "csv")
        df.to_csv(csv)
        return csv
