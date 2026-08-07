"""Point-in-time daily ledger — append-only archive of what the desk showed.

One record per KST date, written once and **never overwritten**. That is the
whole point: a later data revision cannot reach back and change an archived row.

Why this exists (see `research/sota_design.md` §5, restatement canary): every
price series in this repo is loaded with ``auto_adjust=True``, so a 2015 bar
sees the 2026 back-adjusted close, and FRED restates its own history (measured
publication lags: M2SL +57d, CPIAUCSL +43d). Any backtest run later is
contaminated by both. A record written *today* is not — and it cannot be
reconstructed after the fact by anyone, including us. Elapsed wall-clock time
is the only non-replicable input available to this project.

The ledger fits models on nothing and opens no sockets beyond the caches the
desk already serves from. It reads the same payloads the user saw.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from kostolany.blob_cache import pull_blob, push_blob_async
from kostolany.regimes import DISCLAIMER_KO
from kostolany.settings import get_settings

log = logging.getLogger(__name__)

LEDGER_SCHEMA = "ledger_v1"

_GCS_PREFIX = "ledger/day"
_GCS_INDEX_PREFIX = "ledger/index"
_LOCK = threading.Lock()

KST = timezone(timedelta(hours=9))

#: Headlines archived per day. The desk shows ~24; we keep the full desk set.
MAX_NEWS_ITEMS = 48


# --------------------------------------------------------------------------
# paths
# --------------------------------------------------------------------------


_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MONTH_RE = re.compile(r"^\d{4}-\d{2}$")


def _root() -> Path:
    return get_settings().cache_path.parent / "ledger"


def _safe_day(day: str) -> str:
    """Dates land in filenames and GCS keys — reject anything but YYYY-MM-DD."""
    day = str(day).strip()
    if not _DAY_RE.match(day):
        raise ValueError(f"invalid ledger date: {day!r}")
    return day


def _safe_month(month: str) -> str:
    month = str(month).strip()
    if not _MONTH_RE.match(month):
        raise ValueError(f"invalid ledger month: {month!r}")
    return month


def _record_path(day: str) -> Path:
    return _root() / "day" / f"{_safe_day(day)}.json"


def _index_path(month: str) -> Path:
    return _root() / "index" / f"{_safe_month(month)}.json"


def kst_today() -> str:
    """Ledger rows are keyed by Seoul date — the desk's own day boundary."""
    return datetime.now(KST).date().isoformat()


# --------------------------------------------------------------------------
# integrity
# --------------------------------------------------------------------------


def _canonical(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def content_sha256(record: dict[str, Any]) -> str:
    """Hash over the canonical record, excluding the hash field itself.

    Formatting of the stored file is irrelevant — the digest is over sorted,
    compact JSON, so a re-serialised copy verifies identically.
    """
    body = {k: v for k, v in record.items() if k != "content_sha256"}
    return hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# capture — each section reports its own failure rather than aborting the row
# --------------------------------------------------------------------------


def capture_calls() -> tuple[list[dict[str, Any]], str | None]:
    """Every head's call as served, for both watch markets.

    All four heads are archived, not just the shipped `momo` one: scoring a
    head we did not display is the only way to answer "would the other arm
    have done better" without re-running history through revised data.
    """
    from kostolany.api import WATCH_DEFAULT_MODELS, WATCH_MARKETS
    from kostolany.calibration import calibration_payload
    from kostolany.watch_cache import WATCH_PAYLOAD_VERSION, read_watch_cache

    rows: list[dict[str, Any]] = []
    errors: list[str] = []

    for symbol in WATCH_MARKETS:
        try:
            cached = read_watch_cache(
                symbol, WATCH_DEFAULT_MODELS, 360, 2, allow_stale=True
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{symbol}: {exc}")
            continue
        if not cached:
            errors.append(f"{symbol}: no cache")
            continue

        calib = calibration_payload(symbol) or {}
        # Frozen constant, but a redeploy can change it — pin what was on screen.
        confidence_view = calib.get("confidence_view")

        for analyst in cached.get("analysts") or []:
            snap = analyst.get("snapshot") or {}
            if not snap.get("regime"):
                continue
            rows.append(
                {
                    "symbol": symbol,
                    "model": analyst.get("id"),
                    "asof": snap.get("asof"),
                    "regime": snap.get("regime"),
                    "regime_name_ko": snap.get("regime_name_ko"),
                    "confidence": snap.get("confidence"),
                    "probabilities": snap.get("probabilities"),
                    "gauges": snap.get("gauges"),
                    "vote": snap.get("vote"),
                    # Both are recomputable only against back-adjusted prices,
                    # so a later reconstruction would not reproduce them —
                    # exactly the class of fact this archive exists to hold.
                    # (`head_dissent` is deliberately NOT copied: it is a count
                    # over the per-head `regime` strings already archived below,
                    # so a copy would add a second version of one fact.)
                    "flip": snap.get("flip"),
                    "run": snap.get("run"),
                    "transition_score": snap.get("transition_score"),
                    "confidence_view": confidence_view,
                    "payload_version": WATCH_PAYLOAD_VERSION,
                    # How stale the served call was when archived. A row built
                    # from a 30h-old cache is a different fact from a fresh one.
                    "cache_age_hours": cached.get("cache_age_hours"),
                    "cached_at": cached.get("cached_at"),
                }
            )

    return rows, "; ".join(errors) or None


def capture_macro() -> tuple[dict[str, Any] | None, str | None]:
    """Macro board values stamped with the observation date as published today.

    ``observed_at`` is the vintage stamp: when FRED later restates the June
    print, tomorrow's row carries the same ``observed_at`` with a different
    ``value``, and the restatement becomes a diff instead of a silent rewrite.
    """
    try:
        from kostolany.macro_board import get_macro_board

        board = get_macro_board(force=False)
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)

    if not isinstance(board, dict) or not board.get("cards"):
        return None, "empty board"

    series: dict[str, Any] = {}
    for card in board.get("cards") or []:
        cid = str(card.get("id") or "").strip()
        if not cid:
            continue
        points = card.get("series") or []
        last = points[-1] if points else {}
        series[cid] = {
            "value": card.get("value"),
            "unit": card.get("unit"),
            "observed_at": last.get("date") if isinstance(last, dict) else None,
        }

    def _gauge(block: Any) -> dict[str, Any] | None:
        if not isinstance(block, dict):
            return None
        points = block.get("series") or []
        last = points[-1] if points else {}
        return {
            "score": block.get("score"),
            "label": block.get("label"),
            "observed_at": last.get("date") if isinstance(last, dict) else None,
        }

    return (
        {
            "board_asof": board.get("asof"),
            "source": board.get("source"),
            "series": series,
            "fear_greed": _gauge(board.get("fear_greed")),
            "crypto_fear_greed": _gauge(board.get("crypto_fear_greed")),
            "fedwatch": board.get("fedwatch"),
        },
        None,
    )


def capture_news() -> tuple[list[dict[str, Any]], str | None]:
    """Headlines as retrieved today, with their publication timestamps.

    Korean-language financial headlines are not archived anywhere we can buy
    later; Google News RSS is a rolling window. Whatever is not captured on
    the day is gone.
    """
    try:
        from kostolany.connectors.news import fetch_news_desk

        desk = fetch_news_desk(use_cache=True)
    except Exception as exc:  # noqa: BLE001
        return [], str(exc)

    items: list[dict[str, Any]] = []
    for item in (desk.get("items") or [])[:MAX_NEWS_ITEMS]:
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        items.append(
            {
                "id": item.get("id"),
                "title": title[:300],
                "url": item.get("url"),
                "source": item.get("source"),
                "theme": item.get("theme"),
                "published_at": item.get("published_at"),
                "summary": str(item.get("summary") or "")[:280] or None,
            }
        )
    return items, None if items else "no items"


# --------------------------------------------------------------------------
# build / store
# --------------------------------------------------------------------------


def build_record(day: str | None = None) -> dict[str, Any]:
    """Assemble one day's record. Pure capture — no fitting, no writes."""
    day = day or kst_today()
    calls, calls_err = capture_calls()
    macro, macro_err = capture_macro()
    news, news_err = capture_news()

    record: dict[str, Any] = {
        "schema": LEDGER_SCHEMA,
        "date": day,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "calls": calls,
        "macro": macro,
        "news": news,
        # Gaps are recorded, never smoothed over: "the desk showed nothing for
        # news that day" is itself a fact worth archiving.
        "errors": {"calls": calls_err, "macro": macro_err, "news": news_err},
        "disclaimer": DISCLAIMER_KO,
    }
    record["content_sha256"] = content_sha256(record)
    return record


def record_exists(day: str) -> bool:
    """Local first, then GCS — Cloud Run instances are ephemeral, GCS is truth."""
    day = _safe_day(day)
    path = _record_path(day)
    if path.exists():
        return True
    return pull_blob(path, f"{_GCS_PREFIX}/{day}.json")


def get_record(day: str) -> dict[str, Any] | None:
    day = _safe_day(day)
    path = _record_path(day)
    if not path.exists() and not pull_blob(path, f"{_GCS_PREFIX}/{day}.json"):
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _load_index(month: str) -> list[dict[str, Any]]:
    month = _safe_month(month)
    path = _index_path(month)
    if not path.exists():
        pull_blob(path, f"{_GCS_INDEX_PREFIX}/{month}.json")
    if not path.exists():
        return []
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
        return list(rows) if isinstance(rows, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def _append_index(record: dict[str, Any]) -> None:
    day = _safe_day(str(record["date"]))
    month = day[:7]
    rows = [r for r in _load_index(month) if r.get("date") != day]
    rows.append(
        {
            "date": day,
            "recorded_at": record.get("recorded_at"),
            "content_sha256": record.get("content_sha256"),
            "n_calls": len(record.get("calls") or []),
            "n_news": len(record.get("news") or []),
            "has_macro": bool(record.get("macro")),
        }
    )
    rows.sort(key=lambda r: str(r.get("date") or ""))
    path = _index_path(month)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    push_blob_async(path, f"{_GCS_INDEX_PREFIX}/{month}.json", content_type="application/json")


def save_record(record: dict[str, Any]) -> dict[str, Any]:
    """Write once. An existing row for that date is never replaced.

    There is deliberately no ``force`` path. A ledger that can be rewritten
    after the outcome is known is not evidence of anything, and this repo has
    a standing rule against making a failed measurement pass by editing it
    (`AGENTS.md` non-negotiables).
    """
    day = _safe_day(str(record.get("date") or ""))

    with _LOCK:
        if record_exists(day):
            return {"status": "already_recorded", "date": day}
        path = _record_path(day)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        push_blob_async(path, f"{_GCS_PREFIX}/{day}.json", content_type="application/json")
        _append_index(record)

    return {
        "status": "recorded",
        "date": day,
        "content_sha256": record.get("content_sha256"),
        "n_calls": len(record.get("calls") or []),
        "n_news": len(record.get("news") or []),
        "has_macro": bool(record.get("macro")),
    }


def record_day(day: str | None = None) -> dict[str, Any]:
    """Cron entry point: capture today and archive it if there is anything to keep."""
    day = _safe_day(day or kst_today())
    if record_exists(day):
        return {"status": "already_recorded", "date": day}

    record = build_record(day)
    if not record["calls"] and not record["macro"]:
        # Nothing the desk could have shown was reachable — do not burn the
        # day's single write slot on an empty row; let the scheduler retry.
        return {
            "status": "skipped_empty",
            "date": day,
            "errors": record["errors"],
        }
    return save_record(record)


def list_records(*, month: str | None = None, limit: int = 60) -> list[dict[str, Any]]:
    """Index rows, newest first. Defaults to the current KST month."""
    month = month or kst_today()[:7]
    rows = _load_index(month)
    rows.sort(key=lambda r: str(r.get("date") or ""), reverse=True)
    return rows[: max(1, min(limit, 400))]
