"""Point-in-time ledger: immutability, integrity, and honest gap recording."""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from kostolany import api, ledger


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """Point the ledger at a temp root and keep GCS out of the loop."""
    cache_dir = tmp_path / "artifacts" / "cache"
    cache_dir.mkdir(parents=True)
    monkeypatch.setenv("KOSTOLANY_CACHE_DIR", str(cache_dir))
    monkeypatch.setattr(ledger, "pull_blob", lambda *a, **k: False)
    monkeypatch.setattr(ledger, "push_blob_async", lambda *a, **k: None)
    monkeypatch.setattr(ledger, "_root", lambda: tmp_path / "ledger")
    # The recent-window memo outlives a test by 30 minutes by design.
    api._ledger_recent_cache.clear()
    yield
    api._ledger_recent_cache.clear()


def _record(day: str = "2026-08-04", **over) -> dict:
    rec = {
        "schema": ledger.LEDGER_SCHEMA,
        "date": day,
        "recorded_at": "2026-08-04T14:50:00+00:00",
        "calls": [{"symbol": "^GSPC", "model": "momo", "regime": "B2"}],
        "macro": {"series": {"rates": {"value": 4.33, "observed_at": "2026-07-01"}}},
        "news": [{"title": "headline", "url": "https://example.com"}],
        "errors": {"calls": None, "macro": None, "news": None},
        "disclaimer": "교육용",
    }
    rec.update(over)
    rec["content_sha256"] = ledger.content_sha256(rec)
    return rec


# ---------------------------------------------------------------- integrity


def test_content_hash_ignores_formatting_and_key_order():
    rec = _record()
    shuffled = {k: rec[k] for k in sorted(rec, reverse=True)}
    assert ledger.content_sha256(shuffled) == rec["content_sha256"]


def test_content_hash_changes_when_a_value_changes():
    before = _record()
    after = _record(calls=[{"symbol": "^GSPC", "model": "momo", "regime": "A2"}])
    assert before["content_sha256"] != after["content_sha256"]


def test_stored_record_verifies_against_its_own_hash():
    ledger.save_record(_record())
    stored = ledger.get_record("2026-08-04")
    assert stored is not None
    assert ledger.content_sha256(stored) == stored["content_sha256"]


# ------------------------------------------------------------- immutability


def test_second_write_for_same_date_is_a_noop():
    first = ledger.save_record(_record())
    assert first["status"] == "recorded"

    intruder = _record(calls=[{"symbol": "^GSPC", "model": "momo", "regime": "A1"}])
    second = ledger.save_record(intruder)
    assert second["status"] == "already_recorded"

    stored = ledger.get_record("2026-08-04")
    assert stored["calls"][0]["regime"] == "B2"  # original survives
    assert stored["content_sha256"] == first["content_sha256"]


def test_no_force_parameter_exists():
    """A rewritable ledger is not evidence — the escape hatch must not exist."""
    import inspect

    params = inspect.signature(ledger.save_record).parameters
    assert set(params) == {"record"}


def test_record_day_is_idempotent(monkeypatch):
    ledger.save_record(_record("2026-08-04"))
    monkeypatch.setattr(ledger, "build_record", lambda day=None: pytest.fail("rebuilt"))
    assert ledger.record_day("2026-08-04")["status"] == "already_recorded"


# --------------------------------------------------------------- path safety


@pytest.mark.parametrize("bad", ["../../etc/passwd", "2026-8-4", "2026-08-04.json", ""])
def test_traversal_and_malformed_dates_rejected(bad):
    with pytest.raises(ValueError):
        ledger.get_record(bad)


def test_index_month_validated():
    with pytest.raises(ValueError):
        ledger.list_records(month="../..")


# ------------------------------------------------------- capture / gap logic


def test_empty_capture_is_skipped_rather_than_written(monkeypatch):
    monkeypatch.setattr(ledger, "capture_calls", lambda: ([], "no cache"))
    monkeypatch.setattr(ledger, "capture_macro", lambda: (None, "boom"))
    monkeypatch.setattr(ledger, "capture_news", lambda: ([], "no items"))

    out = ledger.record_day("2026-08-05")
    assert out["status"] == "skipped_empty"
    assert out["errors"]["macro"] == "boom"
    assert ledger.get_record("2026-08-05") is None


def test_partial_capture_is_recorded_with_its_gaps(monkeypatch):
    monkeypatch.setattr(
        ledger, "capture_calls", lambda: ([{"symbol": "^GSPC", "regime": "B2"}], None)
    )
    monkeypatch.setattr(ledger, "capture_macro", lambda: (None, "fred down"))
    monkeypatch.setattr(ledger, "capture_news", lambda: ([], "no items"))

    out = ledger.record_day("2026-08-06")
    assert out["status"] == "recorded"

    stored = ledger.get_record("2026-08-06")
    assert stored["macro"] is None
    assert stored["errors"]["macro"] == "fred down"
    assert stored["errors"]["news"] == "no items"


def test_index_lists_recorded_days():
    ledger.save_record(_record("2026-08-04"))
    ledger.save_record(_record("2026-08-06"))

    rows = ledger.list_records(month="2026-08")
    assert [r["date"] for r in rows] == ["2026-08-06", "2026-08-04"]
    assert rows[0]["n_calls"] == 1
    assert rows[0]["has_macro"] is True


def test_macro_capture_stamps_observation_dates(monkeypatch):
    board = {
        "asof": "2026-08-04",
        "source": "fred",
        "cards": [
            {
                "id": "rates",
                "value": 4.33,
                "unit": "%",
                "series": [
                    {"date": "2026-06-01", "value": 4.33},
                    {"date": "2026-07-01", "value": 4.33},
                ],
            },
            {"id": "empty", "value": None, "series": []},
        ],
        "fear_greed": {"score": 62, "label": "Greed", "series": [{"date": "2026-08-03"}]},
    }
    monkeypatch.setattr(
        "kostolany.macro_board.get_macro_board", lambda force=False: board
    )

    macro, err = ledger.capture_macro()
    assert err is None
    # The vintage stamp is the observation date, not the fetch date — a later
    # FRED restatement of the same observed_at becomes a visible diff.
    assert macro["series"]["rates"] == {
        "value": 4.33,
        "unit": "%",
        "observed_at": "2026-07-01",
    }
    assert macro["series"]["empty"]["observed_at"] is None
    assert macro["fear_greed"]["observed_at"] == "2026-08-03"


def test_capture_sections_never_raise(monkeypatch):
    """One dead upstream must not cost the whole day's row."""
    def _boom(*a, **k):
        raise RuntimeError("upstream down")

    monkeypatch.setattr("kostolany.macro_board.get_macro_board", _boom)
    monkeypatch.setattr("kostolany.connectors.news.fetch_news_desk", _boom)

    macro, macro_err = ledger.capture_macro()
    news, news_err = ledger.capture_news()
    assert macro is None and "upstream down" in macro_err
    assert news == [] and "upstream down" in news_err


def test_record_is_valid_json_on_disk():
    ledger.save_record(_record())
    raw = (ledger._root() / "day" / "2026-08-04.json").read_text(encoding="utf-8")
    assert json.loads(raw)["date"] == "2026-08-04"


def test_capture_archives_the_flip_and_run_blocks(monkeypatch):
    """Neither is reconstructable later — back-adjusted prices give other numbers."""
    cached = {
        "cache_age_hours": 0.5,
        "cached_at": "2026-08-06T14:00:00+00:00",
        "analysts": [
            {
                "id": "momo",
                "snapshot": {
                    "regime": "B2",
                    "asof": "2026-08-05",
                    "vote": {"split": "8-0", "tier": "unanimous", "side": "down"},
                    "flip": {"basis": "same_bar_close", "rules": [], "steps": [], "side_flip": None},
                    "run": {"side": "down", "side_bars": 37},
                },
            }
        ],
    }
    monkeypatch.setattr(
        "kostolany.watch_cache.read_watch_cache", lambda *a, **k: cached
    )
    monkeypatch.setattr("kostolany.calibration.calibration_payload", lambda s: None)

    rows, err = ledger.capture_calls()
    assert err is None
    assert rows and all(r["model"] == "momo" for r in rows)
    assert rows[0]["flip"]["basis"] == "same_bar_close"
    assert rows[0]["run"]["side_bars"] == 37
    # head_dissent is a count over the per-head `regime` strings already stored,
    # so it is deliberately not duplicated into the archive.
    assert "head_dissent" not in rows[0]


# ------------------------------------------------------------ /ledger/recent


def _seed_days_ago(offset: int, calls: list[dict]) -> str:
    day = (date.fromisoformat(ledger.kst_today()) - timedelta(days=offset)).isoformat()
    ledger.save_record(_record(day, calls=calls))
    return day


def _recent(days: int = 14) -> dict:
    return TestClient(api.create_app()).get(f"/ledger/recent?days={days}").json()


def test_ledger_recent_is_not_swallowed_by_the_day_route():
    """`/ledger/{day}`'s date pattern is a validator, not a route match."""
    resp = TestClient(api.create_app()).get("/ledger/recent")
    assert resp.status_code == 200


def test_ledger_recent_copies_serving_head_rows_for_watch_markets_only():
    day = _seed_days_ago(
        1,
        [
            {
                "symbol": "^GSPC", "model": "momo", "asof": "2026-08-05", "regime": "B2",
                "vote": {"split": "8-0", "tier": "unanimous", "side": "down"},
            },
            # Archived for later comparison, but never on screen.
            {"symbol": "^GSPC", "model": "hmm", "asof": "2026-08-05", "regime": "A3"},
            # Not a watch market.
            {
                "symbol": "EEM", "model": "momo", "asof": "2026-08-05", "regime": "A1",
                "vote": {"split": "6-2", "tier": "lean", "side": "up"},
            },
            # Fail-closed archive: regime only, no reconstructed split.
            {"symbol": "BTC-USD", "model": "momo", "asof": "2026-08-05", "regime": "A2", "vote": None},
        ],
    )
    body = _recent()

    assert body["n_days"] == 1
    assert body["days"][0]["date"] == day
    calls = body["days"][0]["calls"]
    assert [c["symbol"] for c in calls] == ["^GSPC", "BTC-USD"]
    assert calls[0] == {
        "symbol": "^GSPC", "asof": "2026-08-05", "regime": "B2",
        "split": "8-0", "tier": "unanimous", "side": "down",
    }
    assert calls[1] == {"symbol": "BTC-USD", "asof": "2026-08-05", "regime": "A2"}


def test_ledger_recent_never_scores_and_never_aggregates():
    """T0 forbids scoring; this view exists because showing is not scoring."""
    _seed_days_ago(1, [{"symbol": "^GSPC", "model": "momo", "regime": "B2", "vote": None}])
    _seed_days_ago(2, [{"symbol": "^GSPC", "model": "momo", "regime": "A2", "vote": None}])
    body = _recent()

    assert body["scored"] is False
    assert body["prereg_doc"] == api.LEDGER_PREREG_DOC
    # Row counts and the window's edges only — no rate, streak or agreement.
    assert set(body) == {"days", "n_days", "first_date", "scored", "prereg_doc", "disclaimer"}
    for row in body["days"]:
        for call in row["calls"]:
            assert set(call) <= {"symbol", "asof", "regime", "split", "tier", "side"}


def test_ledger_recent_orders_newest_first_and_reports_the_window_start():
    days = [_seed_days_ago(n, [{"symbol": "^GSPC", "model": "momo", "regime": "B2", "vote": None}])
            for n in (1, 2, 4)]
    body = _recent()

    assert [d["date"] for d in body["days"]] == sorted(days, reverse=True)
    assert body["first_date"] == min(days)


def test_ledger_recent_window_excludes_days_outside_it():
    _seed_days_ago(1, [{"symbol": "^GSPC", "model": "momo", "regime": "B2", "vote": None}])
    _seed_days_ago(9, [{"symbol": "^GSPC", "model": "momo", "regime": "A2", "vote": None}])

    assert _recent(days=14)["n_days"] == 2
    api._ledger_recent_cache.clear()
    assert _recent(days=3)["n_days"] == 1


def test_ledger_recent_answers_normally_on_an_empty_archive():
    body = _recent()
    assert body["n_days"] == 0
    assert body["days"] == []
    assert body["first_date"] is None
    assert body["scored"] is False


def test_ledger_recent_reads_each_date_once_per_ttl(monkeypatch):
    """One GCS pull per archived date is the whole cost — do not pay it per request."""
    _seed_days_ago(1, [{"symbol": "^GSPC", "model": "momo", "regime": "B2", "vote": None}])
    reads: list[str] = []
    real = ledger.get_record
    monkeypatch.setattr(ledger, "get_record", lambda d: (reads.append(d), real(d))[1])

    _recent()
    _recent()
    assert reads == [reads[0]]
