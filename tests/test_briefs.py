from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kostolany import briefs, newsletter
from kostolany.api import create_app


@pytest.fixture()
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("KOSTOLANY_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("NEWSLETTER_CRON_SECRET", "test-secret")
    monkeypatch.setattr(briefs, "push_blob_async", lambda *a, **k: None)
    monkeypatch.setattr(briefs, "pull_blob", lambda *a, **k: False)
    monkeypatch.setattr(newsletter, "push_blob_async", lambda *a, **k: None)
    monkeypatch.setattr(newsletter, "pull_blob", lambda *a, **k: False)
    return tmp_path


def test_daily_card_save(store: Path) -> None:
    card = briefs.build_daily_card_from_context(
        {
            "markets": [
                {
                    "symbol": "^GSPC",
                    "label": "US",
                    "regime": "A2",
                    "regime_name": "동행",
                    "confidence": 0.42,
                }
            ],
            "headlines": [{"title": "Fed holds", "theme": "money"}],
            "priority_summary": "Rates steady.",
        }
    )
    saved = briefs.save_brief(card)
    assert saved["kind"] == "daily"
    assert "A2" in saved["body"]["ko"]
    assert briefs.get_brief(saved["slug"])["slug"] == saved["slug"]
    assert len(briefs.list_briefs(kind="daily")) == 1


def test_api_briefs_read(store: Path) -> None:
    """Publishing briefs via API was retired with the email newsletter."""
    client = TestClient(create_app())
    assert client.post("/briefs", json={}).status_code == 405
    card = briefs.build_daily_card_from_context(
        {
            "markets": [{"label": "US", "regime": "A2", "regime_name": "동행"}],
            "headlines": [],
            "priority_summary": "ok",
        }
    )
    briefs.save_brief(card)
    got = client.get(f"/briefs/{card['slug']}")
    assert got.status_code == 200
    assert got.json()["kind"] == "daily"
