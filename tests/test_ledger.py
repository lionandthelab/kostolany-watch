"""Point-in-time ledger: immutability, integrity, and honest gap recording."""

from __future__ import annotations

import json

import pytest

from kostolany import ledger


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """Point the ledger at a temp root and keep GCS out of the loop."""
    cache_dir = tmp_path / "artifacts" / "cache"
    cache_dir.mkdir(parents=True)
    monkeypatch.setenv("KOSTOLANY_CACHE_DIR", str(cache_dir))
    monkeypatch.setattr(ledger, "pull_blob", lambda *a, **k: False)
    monkeypatch.setattr(ledger, "push_blob_async", lambda *a, **k: None)
    monkeypatch.setattr(ledger, "_root", lambda: tmp_path / "ledger")
    yield


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
