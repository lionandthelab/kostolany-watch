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


def test_api_briefs_auth(store: Path) -> None:
    client = TestClient(create_app())
    assert client.post("/briefs", json={}).status_code in (401, 422)
    payload = {
        "slug": "weekly-2026-08-01",
        "kind": "weekly",
        "date": "2026-08-01",
        "title": {"ko": "테스트", "en": "Test"},
        "body": {"ko": "<p>본문</p>", "en": "<p>Body</p>"},
        "dispatch": False,
    }
    bad = client.post("/briefs", json=payload)
    assert bad.status_code == 401
    ok = client.post("/briefs", json=payload, headers={"X-Cron-Secret": "test-secret"})
    assert ok.status_code == 200
    got = client.get("/briefs/weekly-2026-08-01")
    assert got.status_code == 200
    assert got.json()["title"]["ko"] == "테스트"
