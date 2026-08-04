from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kostolany import push_notify
from kostolany.api import create_app


@pytest.fixture()
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("KOSTOLANY_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("NEWSLETTER_CRON_SECRET", "test-secret")
    monkeypatch.setenv("VAPID_PUBLIC_KEY", "BTestPublicKeyNotReal000000000000000000000000000")
    monkeypatch.setenv("VAPID_PRIVATE_KEY", "dGVzdA")  # not used when send mocked
    monkeypatch.setattr(push_notify, "push_blob_async", lambda *a, **k: None)
    monkeypatch.setattr(push_notify, "pull_blob", lambda *a, **k: False)
    return tmp_path / "push" / "subscriptions.jsonl"


def test_load_subscriptions_pull_blob_arg_order(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: pull_blob(local, gcs_path) — swapped args crashed Cloud Run subscribe."""
    monkeypatch.setenv("KOSTOLANY_CACHE_DIR", str(tmp_path))
    calls: list[tuple[object, object]] = []

    def _pull(local: object, blob: object) -> bool:
        calls.append((local, blob))
        return False

    monkeypatch.setattr(push_notify, "pull_blob", _pull)
    push_notify.load_subscriptions()
    assert calls, "pull_blob should be invoked"
    local, blob = calls[0]
    assert isinstance(local, Path)
    assert blob == "push/subscriptions.jsonl"


def test_upsert_and_deactivate(store: Path) -> None:
    r = push_notify.upsert_subscription(
        {
            "endpoint": "https://push.example/sub/1",
            "keys": {"p256dh": "abc", "auth": "def"},
            "hour_kst": 22,
            "locale": "ko",
        }
    )
    assert r["status"] == "created"
    rows = push_notify.load_subscriptions()
    assert len(rows) == 1
    assert rows[0]["hour_kst"] == 22
    push_notify.deactivate_subscription("https://push.example/sub/1")
    assert push_notify.load_subscriptions()[0]["active"] is False


def test_api_push_and_newsletter_retired(store: Path) -> None:
    client = TestClient(create_app())
    assert client.post("/newsletter/subscribe", json={"email": "a@b.com"}).status_code == 410
    assert client.post("/newsletter/dispatch").status_code == 410

    ok = client.post(
        "/push/subscribe",
        json={
            "endpoint": "https://push.example/sub/2",
            "keys": {"p256dh": "p", "auth": "a"},
            "hour_kst": 9,
            "locale": "en",
        },
    )
    assert ok.status_code == 200

    bad = client.post("/push/dispatch")
    assert bad.status_code == 401

    # Avoid real Web Push network calls
    import kostolany.push_notify as pn_mod

    pn_mod._send_one = lambda sub, payload: "ok"  # type: ignore[method-assign]
    dispatched = client.post(
        "/push/dispatch?force=true",
        headers={"X-Cron-Secret": "test-secret"},
    )
    assert dispatched.status_code == 200
    assert dispatched.json().get("ok") is True


def test_build_daily_metric_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        push_notify,
        "read_watch_cache",
        lambda *a, **k: {
            "analysts": [{"id": "momo", "snapshot": {"regime": "B2", "regime_name_ko": "동행(하락)"}}]
        },
        raising=False,
    )
    # Patch where used
    import kostolany.watch_cache as wc

    monkeypatch.setattr(
        wc,
        "read_watch_cache",
        lambda *a, **k: {
            "analysts": [{"id": "momo", "snapshot": {"regime": "B2", "regime_name_ko": "동행(하락)"}}]
        },
    )
    payload = push_notify.build_daily_metric_payload()
    assert "B2" in payload["body_ko"]
    assert "/watch" in payload["url"]
