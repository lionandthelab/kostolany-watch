"""FastAPI inference service for Kostolany Watch."""

from __future__ import annotations

import threading
from contextlib import asynccontextmanager
from functools import lru_cache
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from kostolany.engine import KostolanyEngine
from kostolany.regimes import DISCLAIMER_KO, REGIME_META, Regime
from kostolany.watch_cache import (
    mark_refresh_started,
    read_watch_cache,
    refresh_cooldown_remaining,
    write_watch_cache,
)


MODEL_PATTERN = "^(hmm|gbm|ensemble|tsfm|ensemble_v3)$"
WATCH_MARKETS = ("KS11", "^GSPC", "BTC-USD")
WATCH_DEFAULT_MODELS = "hmm,gbm,tsfm"
WATCH_DEFAULT_LIMIT = 360
WATCH_DEFAULT_STRIDE = 2

_watch_bg_lock = threading.Lock()
_watch_refreshing: set[str] = set()
_watch_warmup_state: dict[str, Any] = {
    "running": False,
    "done": 0,
    "total": 0,
    "current": None,
    "cached": [],
    "errors": [],
}



class SnapshotResponse(BaseModel):
    symbol: str
    asof: str
    regime: str
    regime_name_ko: str
    confidence: float
    probabilities: dict[str, float]
    gauges: dict[str, float]
    egg: dict[str, float]
    action_ko: str
    next_likely: list[dict[str, Any]]
    disclaimer: str = DISCLAIMER_KO
    transition_score: float | None = None
    evidence: list[dict[str, Any]] | None = None


class RegimeInfo(BaseModel):
    code: str
    name_ko: str
    name_en: str
    action_ko: str
    color: str
    egg_x: float
    egg_y: float


@lru_cache(maxsize=12)
def _engine_for(symbol: str, model_kind: str) -> KostolanyEngine:
    eng = KostolanyEngine(model_kind=model_kind)  # type: ignore[arg-type]
    if symbol.upper() in {"SYNTH", "SYNTHETIC"}:
        eng.fit_synthetic()
    else:
        try:
            eng.fit_symbol(symbol)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=f"Failed to load {symbol}: {exc}") from exc
    return eng


def _build_one_analyst(symbol: str, mid: str, limit: int, stride: int) -> dict[str, Any]:
    eng = _engine_for(symbol, mid)
    try:
        snap = eng.snapshot()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "id": mid,
        "snapshot": SnapshotResponse(**snap.__dict__).model_dump(),
        "replay": eng.replay_dict(limit=limit, stride=stride),
    }


def _build_watch_body(
    symbol: str,
    ids: list[str],
    limit: int,
    stride: int,
) -> dict[str, Any]:
    analysts = [_build_one_analyst(symbol, mid, limit, stride) for mid in ids]
    return {"symbol": symbol, "analysts": analysts, "disclaimer": DISCLAIMER_KO}


def _parse_model_ids(models: str) -> list[str]:
    ids = [m.strip() for m in models.split(",") if m.strip()]
    if not ids:
        raise HTTPException(status_code=400, detail="models required")
    for mid in ids:
        if mid not in {"hmm", "gbm", "ensemble", "tsfm", "ensemble_v3"}:
            raise HTTPException(status_code=400, detail=f"Unknown model: {mid}")
    return ids


def _watch_cache_key(symbol: str, models_key: str, limit: int, stride: int) -> str:
    return f"{symbol}|{models_key}|{limit}|{stride}"


def _schedule_watch_rebuild(symbol: str, ids: list[str], limit: int, stride: int) -> bool:
    models_key = ",".join(ids)
    key = _watch_cache_key(symbol, models_key, limit, stride)
    with _watch_bg_lock:
        if key in _watch_refreshing:
            return False
        _watch_refreshing.add(key)

    def _run() -> None:
        try:
            # Clear only this process cache so refit picks fresh data
            _engine_for.cache_clear()
            body = _build_watch_body(symbol, ids, limit, stride)
            write_watch_cache(symbol, models_key, limit, stride, body, refreshed=True)
        except Exception as exc:  # noqa: BLE001
            with _watch_bg_lock:
                errs = _watch_warmup_state.setdefault("errors", [])
                if isinstance(errs, list):
                    errs.append({"symbol": symbol, "error": str(exc)[:200]})
                    _watch_warmup_state["errors"] = errs[-20:]
        finally:
            with _watch_bg_lock:
                _watch_refreshing.discard(key)

    threading.Thread(target=_run, name=f"watch-refresh-{symbol}", daemon=True).start()
    return True


def warmup_watch_markets(*, force: bool = False) -> dict[str, Any]:
    """Precompute egg-tab markets (KS11 / SPX / BTC) in background."""
    with _watch_bg_lock:
        if _watch_warmup_state.get("running"):
            return watch_warmup_status()
        _watch_warmup_state.update(
            {
                "running": True,
                "done": 0,
                "total": len(WATCH_MARKETS),
                "current": None,
                "errors": [],
            }
        )

    ids = _parse_model_ids(WATCH_DEFAULT_MODELS)

    def _run() -> None:
        try:
            for sym in WATCH_MARKETS:
                with _watch_bg_lock:
                    _watch_warmup_state["current"] = sym
                cached = read_watch_cache(
                    sym,
                    WATCH_DEFAULT_MODELS,
                    WATCH_DEFAULT_LIMIT,
                    WATCH_DEFAULT_STRIDE,
                    allow_stale=True,
                )
                if cached is not None and not cached.get("stale") and not force:
                    with _watch_bg_lock:
                        _watch_warmup_state["done"] = int(_watch_warmup_state.get("done") or 0) + 1
                    continue
                key = _watch_cache_key(sym, WATCH_DEFAULT_MODELS, WATCH_DEFAULT_LIMIT, WATCH_DEFAULT_STRIDE)
                while True:
                    with _watch_bg_lock:
                        busy = key in _watch_refreshing
                    if not busy:
                        break
                    import time

                    time.sleep(0.4)
                with _watch_bg_lock:
                    _watch_refreshing.add(key)
                try:
                    body = _build_watch_body(sym, ids, WATCH_DEFAULT_LIMIT, WATCH_DEFAULT_STRIDE)
                    write_watch_cache(
                        sym,
                        WATCH_DEFAULT_MODELS,
                        WATCH_DEFAULT_LIMIT,
                        WATCH_DEFAULT_STRIDE,
                        body,
                        refreshed=True,
                    )
                except Exception as exc:  # noqa: BLE001
                    with _watch_bg_lock:
                        errs = _watch_warmup_state.setdefault("errors", [])
                        if isinstance(errs, list):
                            errs.append({"symbol": sym, "error": str(exc)[:200]})
                finally:
                    with _watch_bg_lock:
                        _watch_refreshing.discard(key)
                        _watch_warmup_state["done"] = int(_watch_warmup_state.get("done") or 0) + 1
        finally:
            with _watch_bg_lock:
                _watch_warmup_state["running"] = False
                _watch_warmup_state["current"] = None
            # After egg markets are warm, fill sector flows (avoid CPU fight).
            try:
                from kostolany.flows import warmup_all_flows

                warmup_all_flows(force=False)
            except Exception:  # noqa: BLE001
                pass

    threading.Thread(target=_run, name="watch-warmup", daemon=True).start()
    return watch_warmup_status()


def watch_warmup_status() -> dict[str, Any]:
    with _watch_bg_lock:
        cached = []
        for sym in WATCH_MARKETS:
            hit = read_watch_cache(
                sym,
                WATCH_DEFAULT_MODELS,
                WATCH_DEFAULT_LIMIT,
                WATCH_DEFAULT_STRIDE,
                allow_stale=True,
            )
            if hit is not None:
                cached.append(sym)
        return {
            **_watch_warmup_state,
            "total": int(_watch_warmup_state.get("total") or 0) or len(WATCH_MARKETS),
            "cached": cached,
            "refreshing": sorted(_watch_refreshing),
        }


def create_app() -> FastAPI:
    app = FastAPI(
        title="Kostolany Watch API",
        description="Regime detection for the Kostolany egg — research/education only.",
        version="0.2.0",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/regimes", response_model=list[RegimeInfo])
    def list_regimes() -> list[RegimeInfo]:
        out = []
        for r, m in REGIME_META.items():
            out.append(
                RegimeInfo(
                    code=m.code,
                    name_ko=m.name_ko,
                    name_en=m.name_en,
                    action_ko=m.action_ko,
                    color=m.color,
                    egg_x=m.egg_x,
                    egg_y=m.egg_y,
                )
            )
        return out

    @app.get("/snapshot", response_model=SnapshotResponse)
    def snapshot(
        symbol: str = Query("SYNTH", description="SYNTH | KS11 | Yahoo ticker"),
        model: str = Query("hmm", pattern=MODEL_PATTERN),
        asof: str | None = Query(None, description="Optional YYYY-MM-DD for historical snapshot"),
    ) -> SnapshotResponse:
        eng = _engine_for(symbol, model)
        try:
            snap = eng.snapshot(asof=asof)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return SnapshotResponse(**snap.__dict__)

    @app.get("/history")
    def history(
        symbol: str = Query("SYNTH"),
        model: str = Query("hmm", pattern=MODEL_PATTERN),
        limit: int = Query(400, ge=50, le=5000),
    ) -> dict[str, Any]:
        eng = _engine_for(symbol, model)
        df = eng.history().tail(limit)
        records = []
        for ts, row in df.iterrows():
            records.append(
                {
                    "date": str(pd_timestamp(ts).date()),
                    "regime": Regime(int(row["regime"])).name,
                    "probabilities": {Regime(i).name: float(row[f"p{i}"]) for i in range(6)},
                    "gauges": {
                        k: float(row[k])
                        for k in ("volume", "participation", "money", "sentiment")
                        if k in row
                    },
                }
            )
        return {"symbol": eng.symbol, "points": records, "disclaimer": DISCLAIMER_KO}

    @app.get("/replay")
    def replay(
        symbol: str = Query("SYNTH"),
        model: str = Query("hmm", pattern=MODEL_PATTERN),
        limit: int = Query(400, ge=20, le=5000),
        stride: int = Query(1, ge=1, le=20),
    ) -> dict[str, Any]:
        """Past egg path for scrubbing / replay UX."""
        eng = _engine_for(symbol, model)
        return eng.replay_dict(limit=limit, stride=stride)

    @app.get("/watch")
    def watch_bundle(
        symbol: str = Query("KS11"),
        models: str = Query("hmm,gbm,tsfm", description="Comma-separated model ids"),
        limit: int = Query(360, ge=20, le=5000),
        stride: int = Query(2, ge=1, le=20),
        refresh: bool = Query(False, description="Kick background refresh; return cache now"),
        peek: bool = Query(False, description="Return cache only; 204 if miss"),
    ) -> Any:
        """Serve cached watch payload. Prefer stale cache; refresh is non-blocking."""
        ids = _parse_model_ids(models)
        models_key = ",".join(ids)
        key = _watch_cache_key(symbol, models_key, limit, stride)

        if peek and not refresh:
            cached = read_watch_cache(symbol, models_key, limit, stride, allow_stale=True)
            if cached is None:
                return Response(status_code=204)
            cached["refreshing"] = key in _watch_refreshing
            return cached

        cached = read_watch_cache(symbol, models_key, limit, stride, allow_stale=True)

        if refresh:
            remaining = refresh_cooldown_remaining(symbol, models_key, limit, stride)
            if remaining > 0 and cached is not None:
                mins = int(remaining // 60) + (1 if remaining % 60 else 0)
                out = dict(cached)
                out["refreshing"] = False
                out["detail"] = f"리프레시는 1시간에 한 번만 가능합니다. 약 {mins}분 후."
                return out
            if cached is not None:
                started = _schedule_watch_rebuild(symbol, ids, limit, stride)
                if started:
                    mark_refresh_started(symbol, models_key, limit, stride)
                out = dict(cached)
                out["refreshing"] = True
                out["refresh_started"] = started
                out["can_refresh"] = False
                return out
            # cold miss on forced refresh — must compute once
            body = _build_watch_body(symbol, ids, limit, stride)
            return write_watch_cache(symbol, models_key, limit, stride, body, refreshed=True)

        if cached is not None:
            if cached.get("stale"):
                _schedule_watch_rebuild(symbol, ids, limit, stride)
                cached["refreshing"] = True
            else:
                cached["refreshing"] = key in _watch_refreshing
            return cached

        # Cold miss: compute sync (warmup should prevent this for main markets)
        body = _build_watch_body(symbol, ids, limit, stride)
        return write_watch_cache(symbol, models_key, limit, stride, body, refreshed=False)

    @app.get("/watch/one")
    def watch_one(
        symbol: str = Query("KS11"),
        model: str = Query("hmm", pattern=MODEL_PATTERN),
        limit: int = Query(360, ge=20, le=5000),
        stride: int = Query(2, ge=1, le=20),
    ) -> dict[str, Any]:
        """Single analyst payload for progressive UI loading."""
        analyst = _build_one_analyst(symbol, model, limit, stride)
        return {"symbol": symbol, "analyst": analyst, "disclaimer": DISCLAIMER_KO}

    @app.post("/watch/begin-refresh")
    def watch_begin_refresh(
        symbol: str = Query("KS11"),
        models: str = Query("hmm,gbm,tsfm"),
        limit: int = Query(360, ge=20, le=5000),
        stride: int = Query(2, ge=1, le=20),
    ) -> Any:
        """Kick background refresh; keep serving existing cache."""
        ids = _parse_model_ids(models)
        models_key = ",".join(ids)
        remaining = refresh_cooldown_remaining(symbol, models_key, limit, stride)
        cached = read_watch_cache(symbol, models_key, limit, stride, allow_stale=True)
        if remaining > 0:
            mins = int(remaining // 60) + (1 if remaining % 60 else 0)
            return JSONResponse(
                status_code=429,
                content={
                    "detail": f"리프레시는 1시간에 한 번만 가능합니다. 약 {mins}분 후에 다시 시도하세요.",
                    "retry_after_seconds": int(remaining),
                    "cached": cached,
                },
                headers={"Retry-After": str(max(1, int(remaining)))},
            )
        started = _schedule_watch_rebuild(symbol, ids, limit, stride)
        meta = mark_refresh_started(symbol, models_key, limit, stride)
        return {"ok": True, "refresh_started": started, "cached": cached, **meta}

    @app.post("/watch/seal")
    def watch_seal(
        symbol: str = Query("KS11"),
        models: str = Query("hmm,gbm,tsfm"),
        limit: int = Query(360, ge=20, le=5000),
        stride: int = Query(2, ge=1, le=20),
        refreshed: bool = Query(False),
    ) -> dict[str, Any]:
        """Assemble combined 6h cache from already-warm engines (no refit if cached)."""
        ids = _parse_model_ids(models)
        # Prefer existing cache if present and not forcing a seal rewrite after progressive load
        cached = read_watch_cache(symbol, ",".join(ids), limit, stride, allow_stale=True)
        if cached is not None and not refreshed:
            return cached
        body = _build_watch_body(symbol, ids, limit, stride)
        return write_watch_cache(symbol, ",".join(ids), limit, stride, body, refreshed=refreshed)

    @app.get("/watch/warmup")
    def watch_warmup_get() -> dict[str, Any]:
        return watch_warmup_status()

    @app.post("/watch/warmup")
    def watch_warmup_post(
        force: bool = Query(False),
    ) -> dict[str, Any]:
        return warmup_watch_markets(force=force)

    @app.get("/news")
    def news_desk(
        refresh: bool = Query(False, description="Kick background refresh; return cache now"),
    ) -> dict[str, Any]:
        """Macro desk headlines for money / credit / Korea / sentiment."""
        from kostolany.connectors.news import fetch_news_desk

        return fetch_news_desk(use_cache=True, refresh=refresh)

    @app.get("/flows/sectors")
    def flows_sectors() -> dict[str, Any]:
        from kostolany.flows import list_sectors

        return list_sectors()

    @app.get("/flows/warmup")
    def flows_warmup_status() -> dict[str, Any]:
        from kostolany.flows import warmup_status

        return warmup_status()

    @app.post("/flows/warmup")
    def flows_warmup_start(
        force: bool = Query(False, description="Rebuild even fresh caches"),
    ) -> dict[str, Any]:
        from kostolany.flows import warmup_all_flows

        return warmup_all_flows(force=force)

    @app.get("/flows")
    def flows_sector(
        sector: str = Query("kospi", description="Sector id from /flows/sectors"),
        refresh: bool = Query(False, description="Kick background refresh; return cache now"),
        peek: bool = Query(False, description="Cache only; 204 if miss"),
    ) -> Any:
        """Historical Up/Down + 3-AI ~3m paths. Prefers cache; refresh is non-blocking."""
        from kostolany.flows import build_sector_flow, read_flow_cache

        try:
            if peek and not refresh:
                cached = read_flow_cache(sector)
                if cached is None:
                    return Response(status_code=204)
                return cached
            return build_sector_flow(sector, use_cache=True, refresh=refresh)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=f"Failed to build flow: {exc}") from exc

    return app


def pd_timestamp(ts: Any):
    import pandas as pd

    return pd.Timestamp(ts)


def create_root_app() -> FastAPI:
    """ASGI app with `/api` prefix for Firebase Hosting → Cloud Run rewrites."""

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        # Watch markets first; flows warmup is chained after watch completes.
        try:
            warmup_watch_markets(force=False)
        except Exception:  # noqa: BLE001
            pass
        try:
            from kostolany.connectors.news import fetch_news_desk

            threading.Thread(
                target=lambda: fetch_news_desk(use_cache=True),
                name="news-warmup",
                daemon=True,
            ).start()
        except Exception:  # noqa: BLE001
            pass
        yield

    root = FastAPI(title="Kostolany Watch", version="0.2.0", lifespan=lifespan)
    api = create_app()
    root.mount("/api", api)

    @root.get("/health")
    def root_health() -> dict[str, str]:
        return {"status": "ok"}

    return root


# Local + Cloud Run entrypoint (routes at /api/*)
app = create_root_app()
