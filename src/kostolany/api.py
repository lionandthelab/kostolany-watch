"""FastAPI inference service for Kostolany Watch."""

from __future__ import annotations

import threading
from collections import OrderedDict, deque
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from kostolany.engine import KostolanyEngine, fit_analyst_bundle
from kostolany.regimes import DISCLAIMER_KO, REGIME_META, Regime
from kostolany.watch_cache import (
    mark_refresh_started,
    mark_symbol_refresh_started,
    read_watch_cache,
    refresh_cooldown_remaining,
    symbol_refresh_cooldown_remaining,
    write_watch_cache,
)


MODEL_PATTERN = "^(hmm|gbm|ensemble|tsfm|ensemble_v3)$"
WATCH_MARKETS = ("KS11", "^GSPC", "BTC-USD")
WATCH_DEFAULT_MODELS = "hmm,gbm,tsfm"
WATCH_DEFAULT_LIMIT = 360
WATCH_DEFAULT_STRIDE = 2
MAX_WATCH_QUEUE = max(6, 2 * len(WATCH_MARKETS))

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
_watch_job_lock = threading.Lock()
WatchJob = tuple[str, tuple[str, ...], int, int]
_watch_priority_q: deque[WatchJob] = deque()
_watch_normal_q: deque[WatchJob] = deque()
_watch_queued: set[WatchJob] = set()
_watch_worker_running = False
_engine_cache_lock = threading.Lock()
_engine_cache: OrderedDict[tuple[str, str], KostolanyEngine] = OrderedDict()
_ENGINE_CACHE_SIZE = 12



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


def _engine_for(symbol: str, model_kind: str) -> KostolanyEngine:
    key = (symbol.upper(), model_kind)
    with _engine_cache_lock:
        cached = _engine_cache.pop(key, None)
        if cached is not None:
            _engine_cache[key] = cached
            return cached
    eng = KostolanyEngine(model_kind=model_kind)  # type: ignore[arg-type]
    if symbol.upper() in {"SYNTH", "SYNTHETIC"}:
        eng.fit_synthetic()
    else:
        try:
            eng.fit_symbol(symbol)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=f"Failed to load {symbol}: {exc}") from exc
    with _engine_cache_lock:
        existing = _engine_cache.pop(key, None)
        if existing is not None:
            _engine_cache[key] = existing
            return existing
        _engine_cache[key] = eng
        while len(_engine_cache) > _ENGINE_CACHE_SIZE:
            _engine_cache.popitem(last=False)
    return eng


def _invalidate_engine_symbol(symbol: str) -> None:
    normalized = symbol.upper()
    with _engine_cache_lock:
        for key in [key for key in _engine_cache if key[0] == normalized]:
            _engine_cache.pop(key, None)


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
    shared_ids = {"hmm", "gbm", "tsfm"}
    if len(ids) > 1 and set(ids).issubset(shared_ids):
        engines = fit_analyst_bundle(symbol)
        analysts = []
        for mid in ids:
            eng = engines[mid]
            snap = eng.snapshot()
            analysts.append(
                {
                    "id": mid,
                    "snapshot": SnapshotResponse(**snap.__dict__).model_dump(),
                    "replay": eng.replay_dict(limit=limit, stride=stride),
                }
            )
    else:
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
    """Queue a rebuild on the serial watch worker (priority)."""
    return _enqueue_watch(
        symbol,
        priority=symbol.upper() in {market.upper() for market in WATCH_MARKETS},
        ids=ids,
        limit=limit,
        stride=stride,
    )


def warmup_watch_markets(*, force: bool = False) -> dict[str, Any]:
    """Enqueue egg-tab markets at low priority (ensure_watch jumps the line)."""
    with _watch_job_lock:
        _watch_warmup_state.update(
            {
                "running": True,
                "done": 0,
                "total": len(WATCH_MARKETS),
                "started_at": _watch_warmup_state.get("started_at"),
            }
        )
    for sym in WATCH_MARKETS:
        cached = read_watch_cache(
            sym,
            WATCH_DEFAULT_MODELS,
            WATCH_DEFAULT_LIMIT,
            WATCH_DEFAULT_STRIDE,
            allow_stale=True,
        )
        if cached is not None and not cached.get("stale") and not force:
            continue
        _enqueue_watch(sym, priority=False)
    return watch_warmup_status()


def ensure_watch_market(symbol: str, *, force: bool = False) -> dict[str, Any]:
    """Prioritize one market so tab switches are not stuck behind full warmup."""
    sym = symbol.strip() or "KS11"
    cached = read_watch_cache(
        sym,
        WATCH_DEFAULT_MODELS,
        WATCH_DEFAULT_LIMIT,
        WATCH_DEFAULT_STRIDE,
        allow_stale=True,
    )
    if cached is not None and not force and not cached.get("stale"):
        return {"symbol": sym, "queued": False, "has_cache": True, "status": watch_warmup_status()}
    queued_new = _enqueue_watch(sym, priority=True)
    default_job: WatchJob = (
        sym,
        tuple(_parse_model_ids(WATCH_DEFAULT_MODELS)),
        WATCH_DEFAULT_LIMIT,
        WATCH_DEFAULT_STRIDE,
    )
    with _watch_job_lock:
        queued = queued_new or default_job in _watch_queued
    return {
        "symbol": sym,
        "queued": queued,
        "has_cache": cached is not None,
        "status": watch_warmup_status(),
    }


def _enqueue_watch(
    symbol: str,
    *,
    priority: bool,
    ids: list[str] | None = None,
    limit: int = WATCH_DEFAULT_LIMIT,
    stride: int = WATCH_DEFAULT_STRIDE,
) -> bool:
    global _watch_worker_running
    model_ids = tuple(ids or _parse_model_ids(WATCH_DEFAULT_MODELS))
    job: WatchJob = (symbol, model_ids, limit, stride)
    with _watch_job_lock:
        if job in _watch_queued:
            if priority:
                try:
                    _watch_normal_q.remove(job)
                except ValueError:
                    pass
                if job not in _watch_priority_q:
                    _watch_priority_q.appendleft(job)
            if not _watch_worker_running:
                _watch_worker_running = True
                threading.Thread(target=_watch_worker, name="watch-worker", daemon=True).start()
            return False
        if len(_watch_queued) >= MAX_WATCH_QUEUE:
            if priority and _watch_normal_q:
                evicted = _watch_normal_q.pop()
                _watch_queued.discard(evicted)
            else:
                return False
        _watch_queued.add(job)
        if priority:
            _watch_priority_q.appendleft(job)
        else:
            _watch_normal_q.append(job)
        if not _watch_worker_running:
            _watch_worker_running = True
            threading.Thread(target=_watch_worker, name="watch-worker", daemon=True).start()
        return True


def _watch_worker() -> None:
    global _watch_worker_running
    with _watch_job_lock:
        _watch_warmup_state["running"] = True
        _watch_warmup_state["total"] = len(WATCH_MARKETS)

    while True:
        with _watch_job_lock:
            job: WatchJob | None = None
            if _watch_priority_q:
                job = _watch_priority_q.popleft()
            elif _watch_normal_q:
                job = _watch_normal_q.popleft()
            if job is None:
                _watch_worker_running = False
                _watch_warmup_state["running"] = False
                _watch_warmup_state["current"] = None
                # Fill flows after watch markets are handled (avoid CPU fight).
                try:
                    from kostolany.flows import warmup_all_flows

                    threading.Thread(
                        target=lambda: warmup_all_flows(force=False),
                        name="flows-after-watch",
                        daemon=True,
                    ).start()
                except Exception:  # noqa: BLE001
                    pass
                return
            sym, model_ids, limit, stride = job
            ids = list(model_ids)
            models_key = ",".join(ids)
            _watch_warmup_state["current"] = sym
            _watch_warmup_state["running"] = True

        key = _watch_cache_key(sym, models_key, limit, stride)
        with _watch_bg_lock:
            _watch_refreshing.add(key)
        try:
            # Invalidate only this symbol's point-endpoint engines.
            _invalidate_engine_symbol(sym)
            body = _build_watch_body(sym, ids, limit, stride)
            write_watch_cache(
                sym,
                models_key,
                limit,
                stride,
                body,
                refreshed=True,
            )
        except Exception as exc:  # noqa: BLE001
            with _watch_bg_lock:
                errs = _watch_warmup_state.setdefault("errors", [])
                if isinstance(errs, list):
                    errs.append({"symbol": sym, "error": str(exc)[:200]})
                    _watch_warmup_state["errors"] = errs[-20:]
        finally:
            with _watch_bg_lock:
                _watch_refreshing.discard(key)
            with _watch_job_lock:
                _watch_queued.discard(job)
                cached_n = 0
                for s in WATCH_MARKETS:
                    if read_watch_cache(
                        s,
                        WATCH_DEFAULT_MODELS,
                        WATCH_DEFAULT_LIMIT,
                        WATCH_DEFAULT_STRIDE,
                        allow_stale=True,
                    ):
                        cached_n += 1
                _watch_warmup_state["done"] = cached_n


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
            "queued": sorted(
                _watch_cache_key(sym, ",".join(ids), limit, stride)
                for sym, ids, limit, stride in _watch_queued
            ),
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
            remaining = max(
                refresh_cooldown_remaining(symbol, models_key, limit, stride),
                symbol_refresh_cooldown_remaining(symbol),
            )
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
                    mark_symbol_refresh_started(symbol)
                job: WatchJob = (symbol, tuple(ids), limit, stride)
                with _watch_job_lock:
                    queued = job in _watch_queued
                out = dict(cached)
                accepted = started or queued or key in _watch_refreshing
                out["refreshing"] = accepted
                out["refresh_started"] = started
                out["can_refresh"] = not accepted
                return out
            # cold miss on forced refresh — must compute once
            body = _build_watch_body(symbol, ids, limit, stride)
            mark_symbol_refresh_started(symbol)
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
        remaining = max(
            refresh_cooldown_remaining(symbol, models_key, limit, stride),
            symbol_refresh_cooldown_remaining(symbol),
        )
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
        if not started:
            job: WatchJob = (symbol, tuple(ids), limit, stride)
            with _watch_job_lock:
                already_queued = job in _watch_queued
            if not already_queued:
                return JSONResponse(
                    status_code=503,
                    content={"detail": "작업 대기열이 가득 찼습니다."},
                    headers={"Retry-After": "30"},
                )
            return {
                "ok": True,
                "refresh_started": False,
                "cached": cached,
            }
        meta = mark_refresh_started(symbol, models_key, limit, stride)
        mark_symbol_refresh_started(symbol)
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

    @app.post("/watch/ensure")
    def watch_ensure(
        symbol: str = Query("KS11"),
        force: bool = Query(False),
    ) -> dict[str, Any]:
        return ensure_watch_market(symbol, force=force)

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

    @app.post("/flows/ensure")
    def flows_ensure(
        sector: str = Query("kospi", description="Prioritize this sector in the build queue"),
        force: bool = Query(False),
    ) -> dict[str, Any]:
        from kostolany.flows import ensure_sector

        try:
            return ensure_sector(sector, force=force)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

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

    @app.get("/flows/gauge")
    def flows_gauge(refresh: bool = Query(False)) -> dict[str, Any]:
        """Fear–greed style macro mood gauge for the Flows desk."""
        from kostolany.fear_greed import get_fear_greed

        return get_fear_greed(force=refresh)

    @app.get("/flows/history")
    def flows_history(
        sector: str = Query("kospi", description="Sector id from /flows/sectors"),
        range: str = Query(
            "1y",
            description="History window: 6m | 1y | 3y | 5y",
            pattern="^(6m|1y|3y|5y)$",
        ),
        refresh: bool = Query(False),
    ) -> dict[str, Any]:
        """OHLCV-only history (fast). Use while AI 3m forecasts are still building."""
        from kostolany.flows import build_sector_history

        try:
            return build_sector_history(sector, range_key=range, force=refresh)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=f"Failed to load history: {exc}") from exc

    @app.get("/flows")
    def flows_sector(
        sector: str = Query("kospi", description="Sector id from /flows/sectors"),
        refresh: bool = Query(False, description="Kick background refresh; return cache now"),
        peek: bool = Query(False, description="Cache only; 204 if miss"),
        wait: bool = Query(
            False,
            description="If true, sync-compute on cold miss (heavy). Default false → 202 + bg build",
        ),
    ) -> Any:
        """Historical Up/Down + 3-AI ~3m paths. Prefers cache; never blocks by default."""
        from kostolany.flows import build_sector_flow, read_flow_cache

        try:
            if peek and not refresh:
                cached = read_flow_cache(sector)
                if cached is None:
                    return Response(status_code=204)
                return cached
            # Default wait=false: never sync-compute on the request path (avoids Cloud Run 429)
            data = build_sector_flow(
                sector,
                use_cache=True,
                refresh=refresh,
                wait=wait,
            )
            if data is None:
                return Response(status_code=202)
            return data
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
