"""FastAPI inference service for Kostolany Watch."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from kostolany.engine import KostolanyEngine
from kostolany.regimes import DISCLAIMER_KO, REGIME_META, Regime


MODEL_PATTERN = "^(hmm|gbm|ensemble|tsfm|ensemble_v3)$"


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
    ) -> dict[str, Any]:
        """One round-trip for the watch UI — avoids Hosting/Cloud Run 429 storms."""
        ids = [m.strip() for m in models.split(",") if m.strip()]
        if not ids:
            raise HTTPException(status_code=400, detail="models required")
        for mid in ids:
            if mid not in {"hmm", "gbm", "ensemble", "tsfm", "ensemble_v3"}:
                raise HTTPException(status_code=400, detail=f"Unknown model: {mid}")

        analysts: list[dict[str, Any]] = []
        for mid in ids:
            eng = _engine_for(symbol, mid)
            try:
                snap = eng.snapshot()
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            analysts.append(
                {
                    "id": mid,
                    "snapshot": SnapshotResponse(**snap.__dict__).model_dump(),
                    "replay": eng.replay_dict(limit=limit, stride=stride),
                }
            )
        return {"symbol": symbol, "analysts": analysts, "disclaimer": DISCLAIMER_KO}

    return app


def pd_timestamp(ts: Any):
    import pandas as pd

    return pd.Timestamp(ts)


def create_root_app() -> FastAPI:
    """ASGI app with `/api` prefix for Firebase Hosting → Cloud Run rewrites."""
    root = FastAPI(title="Kostolany Watch", version="0.2.0")
    api = create_app()
    root.mount("/api", api)

    @root.get("/health")
    def root_health() -> dict[str, str]:
        return {"status": "ok"}

    return root


# Local + Cloud Run entrypoint (routes at /api/*)
app = create_root_app()
