"""FastAPI inference service for Kostolany Watch."""

from __future__ import annotations

import threading
import time
from collections import Counter, OrderedDict, deque
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi import Path as FPath
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from kostolany.calibration import calibration_payload
from kostolany.engine import KostolanyEngine, fit_analyst_bundle
from kostolany.regimes import DISCLAIMER_KO, REGIME_META, Regime
from kostolany.watch_cache import (
    WATCH_TTL_HOURS,
    mark_refresh_started,
    mark_symbol_refresh_started,
    read_watch_cache,
    refresh_cooldown_remaining,
    symbol_refresh_cooldown_remaining,
    write_watch_cache,
)


MODEL_PATTERN = "^(hmm|gbm|ensemble|tsfm|ensemble_v3|momo)$"
WATCH_MARKETS = ("^GSPC", "BTC-USD")
WATCH_DEFAULT_MODELS = "momo,hmm,gbm,tsfm"
WATCH_DEFAULT_LIMIT = 360
WATCH_DEFAULT_STRIDE = 2
MAX_WATCH_QUEUE = max(6, 2 * len(WATCH_MARKETS))

#: How often the watch-refresh cron fires. Keep in sync with the -Schedule
#: default in `scripts/setup-watch-refresh-scheduler.ps1`; it is duplicated here
#: only so the freshness thresholds below can be stated as "one cron cycle of
#: grace" instead of a bare number.
WATCH_REFRESH_CYCLE_HOURS = 4.0

#: The ledger cron writes at 23:50 KST (`scripts/setup-ledger-scheduler.ps1`),
#: so today's row is legitimately missing for almost the whole day and a lag of
#: 1 day is the normal state. 2 means a scheduled write did not happen at all.
#: Unlike the caches the ledger has no TTL to derive this from — it is append-
#: once, and "how old is the newest row" is the only staleness it can have.
LEDGER_MAX_LAG_DAYS = 2

#: Same shape of reasoning as the ledger: the card cron writes once a day at
#: 22:00 KST, so a 1-day lag is the normal state for most of the day and 2 means
#: a write was missed. The surface earned its own gauge the hard way — the card
#: generator's route went missing and its job was paused as a "newsletter job",
#: and the newest card sat at 2026-08-02 for five days with nothing complaining.
DAILY_CARD_MAX_LAG_DAYS = 2

#: `/ledger/recent` costs one GCS pull per archived date on a cold instance, so
#: the assembled window is memoised. The archive is append-once and today's row
#: lands at 23:50 KST, so a stale window can only ever be missing the newest row.
LEDGER_RECENT_TTL_S = 1800.0
#: Rendered next to the archive rows. The view shows calls and never scores
#: them; the scoring rules were fixed before any outcome was looked at, and the
#: UI points at that document instead of implying there is nothing to point at.
LEDGER_PREREG_DOC = "docs/LEDGER_SCORING_PREREG_2026-08-07.md"


class PushSubscribeBody(BaseModel):
    endpoint: str = Field(..., min_length=12, max_length=2048)
    keys: dict[str, str]
    hour_kst: int = Field(22, ge=0, le=23)
    locale: str = Field("ko", max_length=16)


class PushUnsubscribeBody(BaseModel):
    endpoint: str = Field(..., min_length=12, max_length=2048)

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
_ledger_recent_lock = threading.Lock()
_ledger_recent_cache: dict[int, tuple[float, dict[str, Any]]] = {}



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
    context_gauges: list[dict[str, Any]] | None = None
    # momo head only: live 8-rule vote block (see engine._vote_block)
    vote: dict[str, Any] | None = None
    # momo head only: same-bar flip distances and the served call's run-length
    # (see engine._flip_block / engine._run_block). Absent on the AI heads.
    flip: dict[str, Any] | None = None
    run: dict[str, Any] | None = None


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


def _head_dissent(analysts: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Where each head is looking, counted — no probability vector is re-read.

    ``side`` comes from the first letter of ``snapshot.regime`` and nothing else,
    so this block can never contradict the regime code printed beside it. The UI
    previously derived agreement from a circular mean of the posterior, which is
    a different quantity from a head's own argmax and can disagree with it on a
    diffuse posterior.

    Counts only. Whether dissent means anything for accuracy is unmeasured
    (P-DJ-1, DESK_JUDGMENT_PREREG §2) — nothing here may carry a rate.

    Returns None below two readable calls: "the heads agree" is not a statement
    one head can make, and a head whose regime code is unreadable is dropped
    rather than guessed at.
    """
    calls: list[dict[str, Any]] = []
    for analyst in analysts:
        regime = str((analyst.get("snapshot") or {}).get("regime") or "")
        if regime[:1] not in {"A", "B"}:
            continue
        calls.append(
            {
                "id": analyst.get("id"),
                "regime": regime,
                "side": "up" if regime[0] == "A" else "down",
            }
        )
    if len(calls) < 2:
        return None

    def _tally(values: list[str]) -> tuple[str | None, int]:
        counts = Counter(values)
        top = max(counts.values())
        leaders = [v for v, c in counts.items() if c == top]
        # A tie has no majority. `n_agree` stays the observed largest bloc — a
        # plain count — so the renderer never has to invent one.
        return (leaders[0] if len(leaders) == 1 else None), top

    n = len(calls)
    side_majority, side_n = _tally([c["side"] for c in calls])
    regime_majority, regime_n = _tally([c["regime"] for c in calls])
    return {
        "n_heads": n,
        "calls": calls,
        "side": {
            "majority": side_majority,
            "n_agree": side_n,
            "unanimous": side_n == n,
            "dissenters": [
                c["id"] for c in calls if side_majority and c["side"] != side_majority
            ],
        },
        "regime": {
            "majority": regime_majority,
            "n_agree": regime_n,
            "unanimous": regime_n == n,
        },
    }


def _build_watch_body(
    symbol: str,
    ids: list[str],
    limit: int,
    stride: int,
) -> dict[str, Any]:
    shared_ids = {"momo", "hmm", "gbm", "tsfm"}
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
    body: dict[str, Any] = {
        "symbol": symbol,
        "analysts": analysts,
        "disclaimer": DISCLAIMER_KO,
    }
    # Aggregated server-side so the panel and the regime code can never be
    # computed two different ways; the key is simply absent below two heads.
    dissent = _head_dissent(analysts)
    if dissent is not None:
        body["head_dissent"] = dissent
    # Measured OOS calibration so the UI can qualify the confidence number
    # instead of rendering an uncalibrated posterior max as a percentage.
    # Symbol-specific: an unmeasured market gets NO block, never a borrowed one.
    calibration = calibration_payload(symbol)
    if calibration is not None:
        body["calibration"] = calibration
    return body


def _parse_model_ids(models: str) -> list[str]:
    ids = [m.strip() for m in models.split(",") if m.strip()]
    if not ids:
        raise HTTPException(status_code=400, detail="models required")
    for mid in ids:
        if mid not in {"hmm", "gbm", "ensemble", "tsfm", "ensemble_v3", "momo"}:
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
    sym = symbol.strip() or "^GSPC"
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


def _watch_market_freshness(symbol: str) -> dict[str, Any]:
    """Age of one market's served payload. Never enqueues — this is a read-only probe."""
    cached = read_watch_cache(
        symbol,
        WATCH_DEFAULT_MODELS,
        WATCH_DEFAULT_LIMIT,
        WATCH_DEFAULT_STRIDE,
        allow_stale=True,
    )
    if cached is None:
        return {
            "symbol": symbol,
            "present": False,
            "cache_age_hours": None,
            "stale": True,
            "asof": None,
            "cached_at": None,
            "ttl_hours": WATCH_TTL_HOURS,
        }
    analysts = cached.get("analysts") or []
    # momo is the serving head, so its asof is the date the desk actually shows.
    pick = next((a for a in analysts if a.get("id") == "momo"), analysts[0] if analysts else {})
    asof = (pick.get("snapshot") or {}).get("asof")
    return {
        "symbol": symbol,
        "present": True,
        "cache_age_hours": cached.get("cache_age_hours"),
        "stale": bool(cached.get("stale")),
        "asof": asof,
        "cached_at": cached.get("cached_at"),
        "ttl_hours": WATCH_TTL_HOURS,
    }


def _prev_month(month: str) -> str:
    year, mon = int(month[:4]), int(month[5:7])
    return f"{year - 1}-12" if mon == 1 else f"{year}-{mon - 1:02d}"


def _ledger_freshness() -> dict[str, Any]:
    """Newest archived day and how far behind KST today it is."""
    from datetime import date

    from kostolany.ledger import kst_today, list_records

    today = kst_today()
    try:
        rows = list_records(month=today[:7], limit=1)
        if not rows:
            # On the 1st–2nd of a month the current index is still empty while
            # the previous month holds the newest row; without this fallback the
            # watchdog would red out at every month boundary.
            rows = list_records(month=_prev_month(today[:7]), limit=1)
    except Exception as exc:  # noqa: BLE001
        return {
            "latest_date": None,
            "days_behind": None,
            "max_lag_days": LEDGER_MAX_LAG_DAYS,
            "error": str(exc)[:200],
        }

    latest = str(rows[0].get("date")) if rows else None
    days_behind: int | None = None
    if latest:
        try:
            days_behind = (date.fromisoformat(today) - date.fromisoformat(latest)).days
        except ValueError:
            days_behind = None
    return {
        "latest_date": latest,
        "days_behind": days_behind,
        "max_lag_days": LEDGER_MAX_LAG_DAYS,
        "today_kst": today,
    }


def _daily_card_freshness(latest_brief: Any) -> dict[str, Any]:
    """Newest published daily card and how far behind KST today it is.

    `latest_brief` is injected rather than imported here so the health endpoint
    stays a pure read: the caller resolves the dependency once and this helper
    can be exercised without reaching into `briefs`.
    """
    from datetime import date

    from kostolany.ledger import kst_today

    today = kst_today()
    try:
        card = latest_brief("daily")
    except Exception as exc:  # noqa: BLE001
        return {
            "latest_date": None,
            "days_behind": None,
            "max_lag_days": DAILY_CARD_MAX_LAG_DAYS,
            "error": str(exc)[:200],
        }

    latest = str(card.get("date")) if card else None
    days_behind: int | None = None
    if latest:
        try:
            days_behind = (date.fromisoformat(today) - date.fromisoformat(latest)).days
        except ValueError:
            days_behind = None
    return {
        "latest_date": latest,
        "days_behind": days_behind,
        "max_lag_days": DAILY_CARD_MAX_LAG_DAYS,
        "today_kst": today,
    }


def ledger_recent_calls(days: int = 14) -> dict[str, Any]:
    """The desk's own archived call strings for the last `days` KST dates.

    A copy, not a scoring pass. No gold label is read, no hit or miss is derived
    and nothing is aggregated across rows — `LEDGER_SCORING_PREREG` forbids
    scoring until its tiers are met, and this view exists precisely because
    showing what was displayed does not require knowing whether it was right.
    `scored` is a constant false and only a passed pre-registration gate may
    ever flip it.

    Filtered to the serving head and the two watch markets: the other three
    heads are archived for later comparison but were never on screen, so putting
    them in a "what we showed that day" view would misdescribe the archive.

    The month index answers which dates exist before any record is fetched, so a
    sparse archive costs one pull per existing day rather than one per day asked
    for.
    """
    from datetime import date, timedelta

    from kostolany.ledger import get_record, kst_today, list_records

    now = time.time()
    with _ledger_recent_lock:
        hit = _ledger_recent_cache.get(days)
        if hit is not None and hit[0] > now:
            return hit[1]

    today = kst_today()
    start = (date.fromisoformat(today) - timedelta(days=days - 1)).isoformat()
    months = [today[:7]] if start[:7] == today[:7] else [today[:7], start[:7]]
    dates: set[str] = set()
    for month in months:
        try:
            rows = list_records(month=month, limit=400)
        except ValueError:  # a malformed index must not take the panel down
            continue
        dates.update(
            d for d in (str(r.get("date") or "") for r in rows) if start <= d <= today
        )

    markets = {m.upper() for m in WATCH_MARKETS}
    archived: list[dict[str, Any]] = []
    for day in sorted(dates, reverse=True)[:days]:
        record = get_record(day)
        if not record:
            continue
        calls: list[dict[str, Any]] = []
        for row in record.get("calls") or []:
            if row.get("model") != "momo":
                continue
            symbol = str(row.get("symbol") or "")
            if symbol.upper() not in markets:
                continue
            call = {
                "symbol": symbol,
                "asof": row.get("asof"),
                "regime": row.get("regime"),
            }
            vote = row.get("vote")
            # A fail-closed archive carries vote=None. That row ships its regime
            # alone rather than a reconstructed split.
            if isinstance(vote, dict):
                call["split"] = vote.get("split")
                call["tier"] = vote.get("tier")
                call["side"] = vote.get("side")
            calls.append(call)
        if calls:
            archived.append({"date": day, "calls": calls})

    payload = {
        "days": archived,
        "n_days": len(archived),
        "first_date": archived[-1]["date"] if archived else None,
        "scored": False,
        "prereg_doc": LEDGER_PREREG_DOC,
        "disclaimer": DISCLAIMER_KO,
    }
    with _ledger_recent_lock:
        _ledger_recent_cache[days] = (now + LEDGER_RECENT_TTL_S, payload)
    return payload


def freshness_report() -> dict[str, Any]:
    """Read-only staleness probe across every cron-owned subsystem.

    Thresholds are the subsystems' own TTLs, not new numbers:

    * watch breaches at ``WATCH_TTL_HOURS`` — that is the exact point where the
      served payload flips to ``stale=true`` in the UI, which is the defect this
      watchdog exists to catch (a payload sat 68h unrefreshed on 2026-08-07
      because nothing but user traffic ever triggered a rebuild).
    * news breaches at its own TTL *plus one cron cycle*. Its 2h TTL is shorter
      than the 4h refresh cadence, so alerting at the bare TTL would fire during
      healthy operation; one cycle of grace means the alert only fires once the
      owning cron has had a full chance to fix it and did not.

    Under normal scheduling the age of both caches peaks just over
    ``WATCH_REFRESH_CYCLE_HOURS``, so neither threshold can be tripped by
    healthy scheduling — only by a run that did not happen.

    * the daily card breaches at two days behind, same reasoning as the ledger:
      its cron writes once a day, so "yesterday" is the normal state for most of
      the day and only a missed write reaches two. This surface is here because
      it is exactly what a self-report would have caught — the card generator
      404'd and its job sat paused from 2026-08-02, and nothing noticed.

    The macro board is reported but cannot breach: no cron owns it (it rebuilds
    lazily on ``/macro``), so a threshold there would be permanently red and
    would only teach the operator to ignore this endpoint.
    """
    from kostolany.briefs import latest_brief
    from kostolany.connectors.news import news_cache_status
    from kostolany.macro_board import board_cache_status

    breaches: list[str] = []

    markets = [_watch_market_freshness(sym) for sym in WATCH_MARKETS]
    for m in markets:
        if not m["present"]:
            breaches.append(f"watch:{m['symbol']} no cache")
        elif m["stale"]:
            breaches.append(
                f"watch:{m['symbol']} {m['cache_age_hours']}h > {WATCH_TTL_HOURS}h"
            )

    news = news_cache_status()
    news_max_age = news["ttl_hours"] + WATCH_REFRESH_CYCLE_HOURS
    news = {**news, "max_age_hours": news_max_age}
    if not news["present"]:
        breaches.append("news no cache")
    elif float(news["cache_age_hours"]) > news_max_age:
        breaches.append(f"news {news['cache_age_hours']}h > {news_max_age}h")

    ledger = _ledger_freshness()
    if ledger.get("error"):
        breaches.append(f"ledger unreadable: {ledger['error']}")
    elif ledger["latest_date"] is None:
        breaches.append("ledger empty")
    elif ledger["days_behind"] is not None and ledger["days_behind"] >= LEDGER_MAX_LAG_DAYS:
        breaches.append(
            f"ledger {ledger['days_behind']}d behind (max {LEDGER_MAX_LAG_DAYS}d)"
        )

    daily_card = _daily_card_freshness(latest_brief)
    if daily_card.get("error"):
        breaches.append(f"daily_card unreadable: {daily_card['error']}")
    elif daily_card["latest_date"] is None:
        breaches.append("daily_card none published")
    elif (
        daily_card["days_behind"] is not None
        and daily_card["days_behind"] >= DAILY_CARD_MAX_LAG_DAYS
    ):
        breaches.append(
            f"daily_card {daily_card['days_behind']}d behind "
            f"(max {DAILY_CARD_MAX_LAG_DAYS}d)"
        )

    return {
        "ok": not breaches,
        "breaches": breaches,
        "watch": markets,
        "news": news,
        "macro": board_cache_status(),
        "ledger": ledger,
        "daily_card": daily_card,
        "refresh_cycle_hours": WATCH_REFRESH_CYCLE_HOURS,
        "disclaimer": DISCLAIMER_KO,
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

    @app.get("/health/freshness")
    def health_freshness() -> Response:
        """Public read-only staleness probe for the watchdog. Triggers no rebuild.

        Always 200, even on breach — never 503. The consumer,
        `.github/scripts/check_freshness.py`, splits its exit code by failure
        mode, and it reads a non-200 as UNREACHABLE ("API or Hosting rewrite is
        down") while a 200 with ``ok=false`` is STALE ("the served data is
        old"). Those are different incidents with different fixes, and 503 would
        collapse the second into the first. The same holds for generic tooling
        (`curl --fail`, uptime checks, `raise_for_status()`), which throws the
        body away on a non-2xx — and the body is the only part that says *what*
        is stale. 503 stays reserved for "this service cannot answer"; a service
        reporting its own staleness has answered. Parse `.ok` / `.breaches`.
        """
        return JSONResponse(
            content=freshness_report(),
            # Firebase Hosting fronts this path; a cached freshness reading is
            # worse than none, since it reports the age of an age.
            headers={"Cache-Control": "no-store, max-age=0"},
        )

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
        symbol: str = Query("^GSPC"),
        models: str = Query("momo,hmm,gbm,tsfm", description="Comma-separated model ids"),
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
        symbol: str = Query("^GSPC"),
        model: str = Query("hmm", pattern=MODEL_PATTERN),
        limit: int = Query(360, ge=20, le=5000),
        stride: int = Query(2, ge=1, le=20),
    ) -> dict[str, Any]:
        """Single analyst payload for progressive UI loading."""
        analyst = _build_one_analyst(symbol, model, limit, stride)
        return {"symbol": symbol, "analyst": analyst, "disclaimer": DISCLAIMER_KO}

    @app.post("/watch/begin-refresh")
    def watch_begin_refresh(
        symbol: str = Query("^GSPC"),
        models: str = Query("momo,hmm,gbm,tsfm"),
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
        symbol: str = Query("^GSPC"),
        models: str = Query("momo,hmm,gbm,tsfm"),
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
        symbol: str = Query("^GSPC"),
        force: bool = Query(False),
    ) -> dict[str, Any]:
        return ensure_watch_market(symbol, force=force)

    @app.post("/watch/refresh")
    def watch_refresh(
        request: Request,
        force: bool = Query(True, description="Rebuild even caches still inside TTL"),
    ) -> dict[str, Any]:
        """Cloud Scheduler: rebuild every watch market on a clock, not on traffic.

        Cloud Run runs this service at minScale=1 with CPU throttling off, so the
        instance never restarts and never re-warms; before this job existed the
        only rebuild trigger was a user hitting `/watch`. Three days of zero
        traffic left a 6h-TTL payload serving 68h stale (measured 2026-08-07).

        Enqueue and return: a full two-market rebuild takes ~7 minutes, far past
        any Cloud Scheduler attempt deadline, so waiting here would guarantee a
        timeout and a retry storm on top of an already-running rebuild.
        """
        from kostolany.connectors.news import kick_news_refresh
        from kostolany.push_notify import cron_secret_ok

        secret = request.headers.get("x-cron-secret") or request.query_params.get("secret")
        if not cron_secret_ok(secret):
            raise HTTPException(status_code=401, detail="unauthorized")

        before = [_watch_market_freshness(sym) for sym in WATCH_MARKETS]
        # `_enqueue_watch` dedupes on the exact job tuple, so a cron run landing
        # on top of an in-flight rebuild is a no-op rather than a second pass.
        status = warmup_watch_markets(force=force)
        # The news desk has the same problem and no cron of its own: its 2h cache
        # is only rebuilt by readers. Kicking it here is what lets
        # /health/freshness ever report ok — this call is non-blocking.
        news_started = kick_news_refresh()

        return {
            "ok": True,
            "forced": force,
            "markets": before,
            "news_refresh_started": news_started,
            "status": status,
            "disclaimer": DISCLAIMER_KO,
        }

    @app.get("/macro")
    def macro_board(
        refresh: bool = Query(False, description="Rebuild macro board (rates, CPI, jobs, FG)"),
    ) -> dict[str, Any]:
        """US macro board for the Macro desk (no Korea panel)."""
        from kostolany.macro_board import get_macro_board

        return get_macro_board(force=refresh)

    @app.get("/news")
    def news_desk(
        refresh: bool = Query(False, description="Kick background refresh; return cache now"),
    ) -> dict[str, Any]:
        """US + crypto headlines (money / credit / crypto / sentiment). EN-primary."""
        from kostolany.connectors.news import fetch_news_desk

        return fetch_news_desk(use_cache=True, refresh=refresh)

    @app.get("/flows/sectors")
    def flows_sectors() -> dict[str, Any]:
        from kostolany.flows import list_sectors

        return list_sectors()

    @app.post("/flows/ensure")
    def flows_ensure(
        sector: str = Query("spx", description="Prioritize this sector in the build queue"),
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
        sector: str = Query("spx", description="Sector id from /flows/sectors"),
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
        sector: str = Query("spx", description="Sector id from /flows/sectors"),
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

    @app.post("/newsletter/subscribe")
    def newsletter_subscribe() -> dict[str, Any]:
        """Retired — use browser push (`/push/subscribe`) instead."""
        raise HTTPException(
            status_code=410,
            detail="newsletter_retired_use_push",
        )

    @app.post("/newsletter/dispatch")
    def newsletter_dispatch() -> dict[str, Any]:
        """Retired email dispatch."""
        raise HTTPException(status_code=410, detail="newsletter_retired_use_push")

    @app.get("/briefs")
    def briefs_list(
        kind: str | None = Query(None, pattern="^(weekly|daily)$"),
        limit: int = Query(40, ge=1, le=200),
    ) -> dict[str, Any]:
        from kostolany.briefs import list_briefs

        return {"items": list_briefs(kind=kind, limit=limit)}  # type: ignore[arg-type]

    @app.get("/briefs/{slug}")
    def briefs_get(slug: str) -> dict[str, Any]:
        from kostolany.briefs import get_brief

        brief = get_brief(slug)
        if brief is None:
            raise HTTPException(status_code=404, detail="not_found")
        return brief

    @app.get("/push/vapid-public-key")
    def push_vapid_public_key() -> dict[str, Any]:
        from kostolany.push_notify import vapid_configured, vapid_public_key

        key = vapid_public_key()
        return {"configured": vapid_configured(), "publicKey": key}

    @app.post("/push/subscribe")
    def push_subscribe(payload: PushSubscribeBody) -> dict[str, Any]:
        from kostolany.push_notify import upsert_subscription, vapid_configured

        if not vapid_configured():
            raise HTTPException(status_code=503, detail="vapid_not_configured")
        try:
            return upsert_subscription(payload.model_dump())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/push/unsubscribe")
    def push_unsubscribe(payload: PushUnsubscribeBody) -> dict[str, Any]:
        from kostolany.push_notify import deactivate_subscription

        try:
            return deactivate_subscription(payload.endpoint)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/push/dispatch")
    def push_dispatch(
        request: Request,
        hour_kst: int | None = Query(None, ge=0, le=23),
        force: bool = Query(False, description="Ignore hour filter; notify all active"),
    ) -> dict[str, Any]:
        """Cloud Scheduler (hourly): send regime metrics to matching push subscribers."""
        from kostolany.push_notify import cron_secret_ok, dispatch_daily

        secret = request.headers.get("x-cron-secret") or request.query_params.get("secret")
        if not cron_secret_ok(secret):
            raise HTTPException(status_code=401, detail="unauthorized")
        return dispatch_daily(hour_kst=hour_kst, force=force)

    @app.post("/briefs/daily/generate")
    def briefs_daily_generate(request: Request) -> dict[str, Any]:
        """Cloud Scheduler (daily): build and save the daily desk card.

        This route went missing while email was being retired. `briefs.
        generate_and_save_daily()` survived, but nothing called it any more and
        the scheduler job still pointed at this path — so it 404'd, and it was
        paused besides, as a "legacy newsletter job" (setup-push-scheduler.ps1).
        It never was one: it generates the on-site card and sends no email.
        Result was a silent stop — the newest daily card sat at 2026-08-02 while
        the desk kept serving.

        `save_brief` upserts on the slug `daily-<date>`, so re-running a day
        replaces its own card rather than accumulating duplicates. That date is
        `date.today()` — UTC on Cloud Run — which is why the job is pinned to
        13:00 UTC: that is 22:00 KST the same calendar day, so the two agree.
        Move it past 15:00 UTC and the card starts carrying tomorrow's date.
        """
        from kostolany.briefs import generate_and_save_daily
        from kostolany.push_notify import cron_secret_ok

        secret = request.headers.get("x-cron-secret") or request.query_params.get("secret")
        if not cron_secret_ok(secret):
            raise HTTPException(status_code=401, detail="unauthorized")
        return generate_and_save_daily()

    @app.post("/ledger/record")
    def ledger_record(
        request: Request,
        date: str | None = Query(
            None,
            description="KST date to archive (defaults to today)",
            pattern=r"^\d{4}-\d{2}-\d{2}$",
        ),
    ) -> dict[str, Any]:
        """Cloud Scheduler (daily): archive today's desk state, write-once.

        Re-running is safe and is a no-op once the day is on file — there is
        no overwrite path by design.
        """
        from kostolany.ledger import record_day
        from kostolany.push_notify import cron_secret_ok

        secret = request.headers.get("x-cron-secret") or request.query_params.get("secret")
        if not cron_secret_ok(secret):
            raise HTTPException(status_code=401, detail="unauthorized")
        try:
            return record_day(date)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/ledger")
    def ledger_list(
        month: str | None = Query(None, pattern=r"^\d{4}-\d{2}$"),
        limit: int = Query(60, ge=1, le=400),
    ) -> dict[str, Any]:
        from kostolany.ledger import kst_today, list_records

        month = month or kst_today()[:7]
        try:
            return {"month": month, "items": list_records(month=month, limit=limit)}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Must stay ABOVE /ledger/{day}: the date pattern there is a validator, not
    # part of the route match, so a later registration would 422 this path.
    @app.get("/ledger/recent")
    def ledger_recent(days: int = Query(14, ge=1, le=31)) -> dict[str, Any]:
        """Unscored archive of what the desk displayed. See ledger_recent_calls."""
        return ledger_recent_calls(days)

    @app.get("/ledger/{day}")
    def ledger_get(day: str = FPath(..., pattern=r"^\d{4}-\d{2}-\d{2}$")) -> dict[str, Any]:
        from kostolany.ledger import get_record

        try:
            record = get_record(day)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if record is None:
            raise HTTPException(status_code=404, detail="not_found")
        return record

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
