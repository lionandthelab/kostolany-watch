"""Watch refresh cron endpoint + the freshness watchdog it exists to satisfy.

Nothing here may reach the network: a real rebuild fits four heads over two
markets (~7 min measured). Every path that would enqueue work is stubbed.
"""

from __future__ import annotations

import json
import os
import time
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kostolany import api, blob_cache, briefs, ledger, watch_cache
from kostolany.connectors import news
from kostolany.settings import get_settings


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Temp cache root, no GCS, no background rebuilds, clean job queues."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True)
    monkeypatch.setenv("CACHE_DIR", str(cache_dir))
    monkeypatch.setenv("KOSTOLANY_CACHE_DIR", str(cache_dir))
    monkeypatch.setenv("NEWSLETTER_CRON_SECRET", "test-secret")
    monkeypatch.delenv("GCS_CACHE_BUCKET", raising=False)
    get_settings.cache_clear()

    monkeypatch.setattr(blob_cache, "pull_if_missing", lambda *a, **k: False)
    monkeypatch.setattr(blob_cache, "push_async", lambda *a, **k: None)
    monkeypatch.setattr(ledger, "pull_blob", lambda *a, **k: False)
    monkeypatch.setattr(ledger, "push_blob_async", lambda *a, **k: None)

    # A no-op worker keeps _enqueue_watch's bookkeeping observable without
    # letting it fit anything.
    monkeypatch.setattr(api, "_watch_worker", lambda: None)
    api._watch_priority_q.clear()
    api._watch_normal_q.clear()
    api._watch_queued.clear()
    api._watch_worker_running = False

    yield cache_dir

    api._watch_priority_q.clear()
    api._watch_normal_q.clear()
    api._watch_queued.clear()
    api._watch_worker_running = False
    get_settings.cache_clear()


def _seed_watch(symbol: str, *, age_hours: float, asof: str = "2026-08-06") -> None:
    """Write a watch envelope aged exactly `age_hours` into the past."""
    path = watch_cache._path(
        symbol, api.WATCH_DEFAULT_MODELS, api.WATCH_DEFAULT_LIMIT, api.WATCH_DEFAULT_STRIDE
    )
    cached_at = time.time() - age_hours * 3600.0
    envelope = {
        "cached_at_epoch": cached_at,
        "last_refresh_epoch": cached_at,
        "body": {
            "symbol": symbol,
            "analysts": [
                {"id": "momo", "snapshot": {"regime": "B2", "asof": asof}},
                {"id": "hmm", "snapshot": {"regime": "A3", "asof": "2026-01-01"}},
            ],
        },
    }
    path.write_text(json.dumps(envelope), encoding="utf-8")


def _seed_news(*, age_hours: float) -> None:
    path = news._cache_path()
    path.write_text(json.dumps({"items": [{"title": "h"}]}), encoding="utf-8")
    stamp = time.time() - age_hours * 3600.0
    os.utime(path, (stamp, stamp))


def _seed_ledger(*, days_behind: int) -> str:
    day = (date.fromisoformat(ledger.kst_today()) - timedelta(days=days_behind)).isoformat()
    record = {
        "schema": ledger.LEDGER_SCHEMA,
        "date": day,
        "recorded_at": "2026-08-06T14:50:00+00:00",
        "calls": [{"symbol": "^GSPC", "model": "momo", "regime": "B2"}],
        "macro": None,
        "news": [],
        "errors": {},
    }
    record["content_sha256"] = ledger.content_sha256(record)
    ledger.save_record(record)
    return day


def _seed_daily_card(*, days_behind: int) -> str:
    day = (date.fromisoformat(ledger.kst_today()) - timedelta(days=days_behind)).isoformat()
    briefs.save_brief(
        {
            "slug": f"daily-{day}",
            "kind": "daily",
            "date": day,
            "title": {"ko": "오늘의 데스크", "en": "Desk today"},
            "body": {"ko": "<p>본문</p>", "en": "<p>Body</p>"},
        }
    )
    return day


def _seed_all_fresh(*, ledger_days_behind: int = 1, card_days_behind: int = 1) -> None:
    for sym in api.WATCH_MARKETS:
        _seed_watch(sym, age_hours=0.5)
    _seed_news(age_hours=0.5)
    _seed_ledger(days_behind=ledger_days_behind)
    _seed_daily_card(days_behind=card_days_behind)


# ------------------------------------------------------------------ refresh


def test_refresh_rejects_missing_and_wrong_secret() -> None:
    client = TestClient(api.create_app())
    assert client.post("/watch/refresh").status_code == 401
    assert client.post("/watch/refresh", headers={"X-Cron-Secret": "nope"}).status_code == 401
    assert client.post("/watch/refresh?secret=nope").status_code == 401


def test_refresh_rejects_when_no_secret_is_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unset secret must not degrade into an open endpoint."""
    monkeypatch.delenv("NEWSLETTER_CRON_SECRET", raising=False)
    client = TestClient(api.create_app())
    assert client.post("/watch/refresh", headers={"X-Cron-Secret": ""}).status_code == 401


def test_refresh_enqueues_every_market_and_reports_current_age(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_watch("^GSPC", age_hours=68.0)
    _seed_watch("BTC-USD", age_hours=68.0)
    monkeypatch.setattr(news, "kick_news_refresh", lambda *a, **k: True)

    client = TestClient(api.create_app())
    resp = client.post("/watch/refresh", headers={"X-Cron-Secret": "test-secret"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["forced"] is True
    assert body["news_refresh_started"] is True

    # Ages are the pre-refresh reading — that is what the cron is diagnosing.
    ages = {m["symbol"]: m["cache_age_hours"] for m in body["markets"]}
    assert set(ages) == set(api.WATCH_MARKETS)
    assert all(a > 60 for a in ages.values())
    assert all(m["stale"] for m in body["markets"])

    queued_symbols = {job[0] for job in api._watch_queued}
    assert queued_symbols == set(api.WATCH_MARKETS)


def test_refresh_is_idempotent_against_an_already_queued_market(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cron run landing on an in-flight rebuild must not double-queue it."""
    monkeypatch.setattr(news, "kick_news_refresh", lambda *a, **k: False)
    client = TestClient(api.create_app())
    headers = {"X-Cron-Secret": "test-secret"}

    client.post("/watch/refresh", headers=headers)
    first = len(api._watch_queued)
    client.post("/watch/refresh", headers=headers)

    assert first == len(api.WATCH_MARKETS)
    assert len(api._watch_queued) == first


def test_refresh_does_not_block_on_the_rebuild(monkeypatch: pytest.MonkeyPatch) -> None:
    """The handler must return without waiting — a full rebuild is ~7 minutes."""
    monkeypatch.setattr(news, "kick_news_refresh", lambda *a, **k: True)

    def _never_call(*a, **k):
        raise AssertionError("refresh must not build a payload inline")

    monkeypatch.setattr(api, "_build_watch_body", _never_call)
    client = TestClient(api.create_app())
    assert client.post("/watch/refresh", headers={"X-Cron-Secret": "test-secret"}).status_code == 200


# ---------------------------------------------------------------- freshness


def test_freshness_is_public_uncached_and_green_when_everything_is_fresh() -> None:
    _seed_all_fresh()
    client = TestClient(api.create_app())
    resp = client.get("/health/freshness")

    assert resp.status_code == 200
    assert "no-store" in resp.headers.get("cache-control", "")
    body = resp.json()
    assert body["ok"] is True, body["breaches"]
    assert body["breaches"] == []
    assert {m["symbol"] for m in body["watch"]} == set(api.WATCH_MARKETS)
    assert body["watch"][0]["asof"] == "2026-08-06"
    assert body["watch"][0]["ttl_hours"] == watch_cache.WATCH_TTL_HOURS
    assert body["news"]["ttl_hours"] == news.NEWS_TTL_HOURS
    assert body["ledger"]["days_behind"] == 1


def test_freshness_reports_the_serving_head_asof_not_the_first_analyst() -> None:
    """`momo` is the shipped head; its asof is the date the desk actually shows."""
    _seed_all_fresh()
    client = TestClient(api.create_app())
    gspc = next(m for m in client.get("/health/freshness").json()["watch"] if m["symbol"] == "^GSPC")
    assert gspc["asof"] == "2026-08-06"


def test_freshness_flags_a_stale_watch_payload_but_still_answers_200() -> None:
    """The 68h incident: served stale, and nothing said so."""
    _seed_all_fresh()
    _seed_watch("^GSPC", age_hours=68.0)

    client = TestClient(api.create_app())
    resp = client.get("/health/freshness")

    assert resp.status_code == 200  # staleness is data, not a transport failure
    body = resp.json()
    assert body["ok"] is False
    assert any("^GSPC" in b for b in body["breaches"])
    assert not any("BTC-USD" in b for b in body["breaches"])
    gspc = next(m for m in body["watch"] if m["symbol"] == "^GSPC")
    assert gspc["stale"] is True
    assert gspc["cache_age_hours"] > watch_cache.WATCH_TTL_HOURS


def test_freshness_tolerates_watch_age_inside_ttl() -> None:
    """One missed 4h cron run still lands inside the 6h TTL — must not alert."""
    _seed_all_fresh()
    for sym in api.WATCH_MARKETS:
        _seed_watch(sym, age_hours=api.WATCH_REFRESH_CYCLE_HOURS + 0.2)

    body = TestClient(api.create_app()).get("/health/freshness").json()
    assert body["ok"] is True, body["breaches"]


def test_freshness_flags_a_missing_watch_cache() -> None:
    _seed_news(age_hours=0.5)
    _seed_ledger(days_behind=1)

    body = TestClient(api.create_app()).get("/health/freshness").json()
    assert body["ok"] is False
    assert all(not m["present"] for m in body["watch"])
    assert all(m["cache_age_hours"] is None for m in body["watch"])
    assert len([b for b in body["breaches"] if "no cache" in b]) == len(api.WATCH_MARKETS)


def test_news_tolerates_its_own_ttl_but_not_a_missed_refresh_cycle() -> None:
    """News TTL (2h) is shorter than the cron cadence (4h) — one cycle of grace."""
    _seed_all_fresh()
    _seed_news(age_hours=news.NEWS_TTL_HOURS + 1.0)
    assert TestClient(api.create_app()).get("/health/freshness").json()["ok"] is True

    _seed_news(age_hours=news.NEWS_TTL_HOURS + api.WATCH_REFRESH_CYCLE_HOURS + 1.0)
    body = TestClient(api.create_app()).get("/health/freshness").json()
    assert body["ok"] is False
    assert any(b.startswith("news") for b in body["breaches"])


def test_freshness_flags_a_lagging_ledger() -> None:
    # The daily cron writes at 23:50 KST, so 1 day behind is the normal state;
    # LEDGER_MAX_LAG_DAYS behind means a scheduled write never happened.
    _seed_all_fresh(ledger_days_behind=api.LEDGER_MAX_LAG_DAYS)

    body = TestClient(api.create_app()).get("/health/freshness").json()
    assert body["ok"] is False
    assert any("ledger" in b for b in body["breaches"])
    assert body["ledger"]["days_behind"] == api.LEDGER_MAX_LAG_DAYS


def test_freshness_accepts_a_ledger_one_day_behind() -> None:
    """Today's row does not exist until 23:50 KST — that is not a failure."""
    _seed_all_fresh(ledger_days_behind=1)
    body = TestClient(api.create_app()).get("/health/freshness").json()
    assert body["ok"] is True, body["breaches"]

    # And the newest row wins once today's is written.
    _seed_ledger(days_behind=0)
    body = TestClient(api.create_app()).get("/health/freshness").json()
    assert body["ledger"]["days_behind"] == 0
    assert body["ok"] is True, body["breaches"]


def test_ledger_falls_back_to_the_previous_month_index() -> None:
    """Without this the watchdog reds out on the 1st of every month."""
    assert api._prev_month("2026-01") == "2025-12"
    assert api._prev_month("2026-08") == "2026-07"


def test_freshness_never_triggers_a_rebuild(monkeypatch: pytest.MonkeyPatch) -> None:
    """A health probe that recomputes the thing it measures is a load generator."""
    _seed_all_fresh()

    def _never_call(*a, **k):
        raise AssertionError("freshness probe must not compute anything")

    monkeypatch.setattr(api, "_build_watch_body", _never_call)
    monkeypatch.setattr(api, "_enqueue_watch", _never_call)
    monkeypatch.setattr("kostolany.macro_board.compute_macro_board", _never_call)
    monkeypatch.setattr(news, "_compute_news_desk", _never_call)

    assert TestClient(api.create_app()).get("/health/freshness").status_code == 200
    assert not api._watch_queued


def test_freshness_honours_the_watchdog_contract_in_both_states() -> None:
    """`.github/scripts/check_freshness.py` parses exactly `{ok: bool, breaches: [str]}`.

    It classifies any non-200 as UNREACHABLE ("the API is down") and a 200 with
    `ok=false` as STALE ("the data is old") — different incidents. Returning 503
    on breach, or dropping either key, would report the wrong one.
    """
    client = TestClient(api.create_app())

    for seed in (lambda: _seed_all_fresh(), lambda: _seed_watch("^GSPC", age_hours=68.0)):
        seed()
        resp = client.get("/health/freshness")
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body["ok"], bool)
        assert isinstance(body["breaches"], list)
        assert all(isinstance(b, str) for b in body["breaches"])
        assert body["ok"] is (not body["breaches"])


def test_plain_health_still_answers_independently_of_freshness() -> None:
    """Existing route must keep its shape — /health/freshness is an addition."""
    client = TestClient(api.create_app())
    assert client.get("/health").json() == {"status": "ok"}


# --------------------------------------------------------------- daily card


def test_daily_card_one_day_behind_is_the_normal_state() -> None:
    """The cron writes once at 22:00 KST, so yesterday's card is not a fault."""
    _seed_all_fresh(card_days_behind=1)
    body = TestClient(api.create_app()).get("/health/freshness").json()
    assert body["daily_card"]["days_behind"] == 1
    assert body["ok"] is True


def test_daily_card_breaches_once_a_write_is_actually_missed() -> None:
    # Seeded in its own test: `latest_brief` returns the newest card, so an
    # older one written alongside a fresh one would never be the one reported.
    _seed_all_fresh(card_days_behind=api.DAILY_CARD_MAX_LAG_DAYS)
    body = TestClient(api.create_app()).get("/health/freshness").json()
    assert body["daily_card"]["days_behind"] == api.DAILY_CARD_MAX_LAG_DAYS
    assert body["ok"] is False
    assert any(b.startswith("daily_card ") for b in body["breaches"])


def test_daily_card_absence_is_a_breach_not_a_silent_pass() -> None:
    """The 2026-08-02 failure mode: nothing published and nothing complaining."""
    for sym in api.WATCH_MARKETS:
        _seed_watch(sym, age_hours=0.5)
    _seed_news(age_hours=0.5)
    _seed_ledger(days_behind=1)

    body = TestClient(api.create_app()).get("/health/freshness").json()
    assert body["daily_card"]["latest_date"] is None
    assert body["ok"] is False
    assert "daily_card none published" in body["breaches"]


# ------------------------------------------------- daily card generation


def test_daily_card_generate_rejects_missing_and_wrong_secret() -> None:
    client = TestClient(api.create_app())
    assert client.post("/briefs/daily/generate").status_code == 401
    assert (
        client.post("/briefs/daily/generate", headers={"X-Cron-Secret": "nope"}).status_code
        == 401
    )


def test_daily_card_generate_publishes_and_clears_the_breach(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The route that went missing: without it the card cron 404s forever."""
    for sym in api.WATCH_MARKETS:
        _seed_watch(sym, age_hours=0.5)
    _seed_news(age_hours=0.5)
    _seed_ledger(days_behind=1)

    # Context gathering reaches for live desk state; the card itself is what is
    # under test, so pin the inputs rather than the network.
    monkeypatch.setattr(
        briefs,
        "gather_daily_context",
        lambda: {"markets": [], "headlines": [], "priority_summary": ""},
    )

    client = TestClient(api.create_app())
    assert client.get("/health/freshness").json()["ok"] is False

    resp = client.post("/briefs/daily/generate", headers={"X-Cron-Secret": "test-secret"})
    assert resp.status_code == 200
    assert resp.json()["kind"] == "daily"

    body = client.get("/health/freshness").json()
    assert body["daily_card"]["days_behind"] == 0
    assert not [b for b in body["breaches"] if b.startswith("daily_card")]
