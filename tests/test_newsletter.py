from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kostolany import newsletter
from kostolany.api import create_app

SAMPLE_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>Guide</title>
  <item>
    <title>Evergreen</title>
    <link>https://kostolany-watch.web.app/guide/kostolany-egg/</link>
    <description>egg</description>
  </item>
  <item>
    <title>주간 #1</title>
    <link>https://kostolany-watch.web.app/guide/weekly-2026-07-31/</link>
    <description>brief</description>
  </item>
</channel></rss>
"""


@pytest.fixture()
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("KOSTOLANY_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("NEWSLETTER_CRON_SECRET", "test-secret")
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.setattr(newsletter, "push_blob_async", lambda *a, **k: None)
    monkeypatch.setattr(newsletter, "pull_blob", lambda *a, **k: False)
    monkeypatch.setattr(newsletter, "send_welcome", lambda *a, **k: None)
    newsletter._RATE.clear()
    return tmp_path / "newsletter" / "subscribers.jsonl"


def test_normalize_email() -> None:
    assert newsletter.normalize_email("  Foo@Bar.COM ") == "foo@bar.com"
    assert newsletter.normalize_email("not-an-email") is None
    assert newsletter.normalize_email("") is None


def test_subscribe_idempotent(store: Path) -> None:
    r1 = newsletter.subscribe("a@example.com", locale="en", source="test", client_key="t1")
    r2 = newsletter.subscribe("A@example.com", locale="en", source="test", client_key="t1")
    assert r1["status"] == "subscribed"
    assert r2["status"] == "already"
    lines = store.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1


def test_honeypot_silent(store: Path) -> None:
    r = newsletter.subscribe(
        "bot@example.com",
        honeypot="http://spam",
        client_key="bot",
    )
    assert r["ok"] is True
    assert not store.exists()


def test_api_subscribe_retired(store: Path) -> None:
    client = TestClient(create_app())
    res = client.post(
        "/newsletter/subscribe",
        json={"email": "reader@example.com", "locale": "ko", "source": "test"},
    )
    assert res.status_code == 410


def test_parse_weekly_from_feed(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeResp:
        status_code = 200
        text = SAMPLE_FEED

        def raise_for_status(self) -> None:
            return None

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url):
            return FakeResp()

    monkeypatch.setattr(newsletter.httpx, "Client", FakeClient)
    brief = newsletter.fetch_latest_weekly_from_feed("https://example.com/feed.xml")
    assert brief is not None
    assert brief["slug"] == "weekly-2026-07-31"


def test_dispatch_dry_run(store: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    newsletter.subscribe("a@example.com", locale="ko", client_key="t2", send_welcome_email=False)

    class FakeResp:
        status_code = 200
        text = SAMPLE_FEED

        def raise_for_status(self) -> None:
            return None

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url):
            return FakeResp()

    monkeypatch.setattr(newsletter.httpx, "Client", FakeClient)
    out = newsletter.dispatch_latest(dry_run=True)
    assert out["status"] == "dry_run"
    assert out["slug"] == "weekly-2026-07-31"
    assert out["subscribers"] == 1


def test_api_dispatch_retired(store: Path) -> None:
    client = TestClient(create_app())
    assert client.post("/newsletter/dispatch").status_code == 410
    assert client.post(
        "/newsletter/dispatch?dry_run=true",
        headers={"X-Cron-Secret": "test-secret"},
    ).status_code == 410
