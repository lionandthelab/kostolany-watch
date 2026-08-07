from __future__ import annotations

import base64
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from kostolany import push_notify
from kostolany.api import create_app


def _public_b64(vapid: Any) -> str:
    """Uncompressed point, urlsafe base64, unpadded — the applicationServerKey form."""
    from cryptography.hazmat.primitives import serialization

    raw = vapid.public_key.public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _sub_keys(seed: int = 0) -> dict[str, str]:
    """A structurally valid PushSubscription key pair.

    `p256dh` is a real uncompressed P-256 point (0x04 || X || Y, 65 bytes) and
    `auth` is 16 bytes, because `push_notify.key_defect` now rejects anything
    else at subscribe time — a row that cannot be encrypted to is not an
    audience member. Placeholders like {"p256dh": "p"} used to sail through and
    were exactly how a 64-byte key sat in the store failing hourly.
    """
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    point = (
        ec.generate_private_key(ec.SECP256R1())
        .public_key()
        .public_bytes(
            encoding=serialization.Encoding.X962,
            format=serialization.PublicFormat.UncompressedPoint,
        )
    )
    return {
        "p256dh": base64.urlsafe_b64encode(point).decode("ascii").rstrip("="),
        "auth": base64.urlsafe_b64encode(bytes((seed + i) % 256 for i in range(16)))
        .decode("ascii")
        .rstrip("="),
    }


@lru_cache(maxsize=1)
def _keypair() -> dict[str, str]:
    """A throwaway VAPID pair in all four spellings push_notify must accept.

    Generated here on purpose: the production private key never belongs in a
    test file. Mirrors scripts/generate_vapid_keys.py.
    """
    from py_vapid import Vapid01

    v = Vapid01()
    v.generate_keys()
    pem = v.private_pem()
    if isinstance(pem, bytes):
        pem = pem.decode("utf-8")
    der_b64 = "".join(
        ln.strip() for ln in pem.splitlines() if ln.strip() and not ln.startswith("-----")
    )
    return {
        "public": _public_b64(v),
        "pem": pem,
        "pem_escaped": pem.replace("\n", "\\n"),
        # What .env and Cloud Run actually store today.
        "base64_pem": base64.urlsafe_b64encode(pem.encode("utf-8")).decode("ascii").rstrip("="),
        "der_base64": der_b64,
    }


@pytest.fixture()
def vapid_keys() -> dict[str, str]:
    return _keypair()


@pytest.fixture()
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    keys = _keypair()
    monkeypatch.setenv("KOSTOLANY_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("NEWSLETTER_CRON_SECRET", "test-secret")
    # A real pair: vapid_configured() now means "can sign", so a placeholder
    # string would make /push/subscribe answer 503 here.
    monkeypatch.setenv("VAPID_PUBLIC_KEY", keys["public"])
    monkeypatch.setenv("VAPID_PRIVATE_KEY", keys["base64_pem"])
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
            "keys": _sub_keys(1),
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


def test_api_push_and_newsletter_retired(store: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = TestClient(create_app())
    assert client.post("/newsletter/subscribe", json={"email": "a@b.com"}).status_code == 410
    assert client.post("/newsletter/dispatch").status_code == 410

    ok = client.post(
        "/push/subscribe",
        json={
            "endpoint": "https://push.example/sub/2",
            "keys": _sub_keys(2),
            "hour_kst": 9,
            "locale": "en",
        },
    )
    assert ok.status_code == 200

    bad = client.post("/push/dispatch")
    assert bad.status_code == 401

    # Avoid real Web Push network calls. Via monkeypatch, not a bare assignment:
    # a raw `pn_mod._send_one = ...` is never undone and silently makes every
    # later test in the file see a send that always succeeds.
    monkeypatch.setattr(push_notify, "_send_one", lambda sub, payload: "ok")
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


VAPID_FORMATS = ("pem", "pem_escaped", "base64_pem", "der_base64")


@pytest.mark.parametrize("fmt", VAPID_FORMATS)
def test_vapid_private_key_normalizes_every_format(fmt: str, vapid_keys: dict[str, str]) -> None:
    """All four stored spellings collapse to the same bare DER body."""
    out = push_notify._normalize_vapid_private_key(vapid_keys[fmt])
    assert out == vapid_keys["der_base64"]
    # The whole point: no armor, no newlines. py_vapid 1.9.4 from_string()
    # b64urldecodes what it is handed, so either would corrupt the ASN.1.
    assert "BEGIN" not in out
    assert out == "".join(out.split())


@pytest.mark.parametrize("fmt", VAPID_FORMATS)
def test_vapid_selftest_signs_and_matches_public_key(
    fmt: str, vapid_keys: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from py_vapid import Vapid

    monkeypatch.setenv("VAPID_PUBLIC_KEY", vapid_keys["public"])
    monkeypatch.setenv("VAPID_PRIVATE_KEY", vapid_keys[fmt])
    state = push_notify.vapid_selftest(refresh=True)
    assert state["ok"] is True, state["reason"]
    assert state["format"] == fmt
    assert state["public_key_matches"] is True
    assert push_notify.vapid_configured() is True
    # Same path pywebpush takes for a str key.
    loaded = Vapid.from_string(private_key=push_notify.vapid_private_key())
    assert _public_b64(loaded) == vapid_keys["public"]


def test_vapid_configured_rejects_unusable_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: it used to mean "env var is non-empty", which is how a key
    py_vapid could not deserialize failed 100% of sends for three days."""
    monkeypatch.setenv("VAPID_PUBLIC_KEY", "BTestPublicKeyNotReal00000000000000000000000000")
    monkeypatch.setenv("VAPID_PRIVATE_KEY", "dGVzdA")
    push_notify.vapid_selftest(refresh=True)
    assert push_notify.vapid_configured() is False
    assert push_notify.vapid_selftest()["reason"]


def test_public_key_mismatch_is_reported(
    vapid_keys: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    other = _public_b64(_other_vapid())
    monkeypatch.setenv("VAPID_PUBLIC_KEY", other)
    monkeypatch.setenv("VAPID_PRIVATE_KEY", vapid_keys["base64_pem"])
    state = push_notify.vapid_selftest(refresh=True)
    assert state["ok"] is True
    assert state["public_key_matches"] is False


@lru_cache(maxsize=1)
def _other_vapid() -> Any:
    from py_vapid import Vapid01

    v = Vapid01()
    v.generate_keys()
    return v


def test_raw_pem_is_why_we_normalize(vapid_keys: dict[str, str]) -> None:
    """py_vapid 1.9.4 from_string() cannot read PEM armor — it strips newlines
    and b64urldecodes the BEGIN line too, yielding "ASN.1 parsing error:
    invalid length". Written tolerantly so a future py_vapid that learns PEM
    passes as well; what must never happen is a silently wrong key.
    """
    from py_vapid import Vapid

    try:
        loaded = Vapid.from_string(private_key=vapid_keys["pem"])
    except Exception:  # noqa: BLE001 — expected on the pinned py_vapid
        return
    assert _public_b64(loaded) == vapid_keys["public"]


def test_dispatch_reports_failure_reasons(store: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The cron always answers 200; the body has to show the failures."""
    for i in (1, 2):
        push_notify.upsert_subscription(
            {
                "endpoint": f"https://fcm.googleapis.example/send/{i}",
                "keys": _sub_keys(2),
                "hour_kst": 22,
            }
        )
    monkeypatch.setattr(
        push_notify, "_send_one", lambda sub, payload: ("error", "http_403", "Push failed: 403")
    )
    monkeypatch.setattr(
        push_notify, "build_daily_metric_payload", lambda: {
            "title_ko": "t", "title_en": "t", "body_ko": "b", "body_en": "b", "url": "u",
        }
    )
    out = push_notify.dispatch_daily(force=True)
    assert out["ok"] is True
    assert out["targets"] == 2
    assert out["sent"] == 0
    assert out["errors"] == 2
    assert out["failures"] == {"http_403": 2}
    assert out["failure_samples"][0]["reason"] == "http_403"
    assert out["failure_samples"][0]["host"] == "fcm.googleapis.example"
    # Endpoint path is a bearer secret — never echoed back.
    assert "/send/1" not in str(out)
    assert out["vapid"]["ok"] is True


def test_dispatch_tolerates_legacy_str_send_result(
    store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    push_notify.upsert_subscription(
        {"endpoint": "https://push.example/sub/9", "keys": _sub_keys(3)}
    )
    monkeypatch.setattr(push_notify, "_send_one", lambda sub, payload: "ok")
    monkeypatch.setattr(
        push_notify, "build_daily_metric_payload", lambda: {
            "title_ko": "t", "title_en": "t", "body_ko": "b", "body_en": "b", "url": "u",
        }
    )
    out = push_notify.dispatch_daily(force=True)
    assert out["sent"] == 1
    assert out["failures"] == {}


def test_dispatch_without_vapid_says_why(
    store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VAPID_PRIVATE_KEY", "not-a-key")
    push_notify.vapid_selftest(refresh=True)
    out = push_notify.dispatch_daily(force=True)
    assert out["ok"] is False
    assert out["error"] == "vapid_not_configured"
    assert out["vapid"]["reason"]


# ------------------------------------------------------- subscription keys


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def test_key_defect_accepts_a_real_subscription() -> None:
    assert push_notify.key_defect(_sub_keys(7)) == ""


def test_key_defect_catches_the_truncated_point_seen_in_production() -> None:
    """The 2026-08-07 row: 64 bytes where a P-256 point needs 65.

    pywebpush reported it as a bare `WebPushException: Invalid p256dh key
    specified` with no HTTP status, so it scored as a transient error and was
    retried every hour indefinitely. Length is the cheap, exact tell.
    """
    good = _sub_keys(8)
    point = base64.urlsafe_b64decode(good["p256dh"] + "=" * (-len(good["p256dh"]) % 4))
    assert len(point) == 65 and point[0] == 0x04

    truncated = {**good, "p256dh": _b64u(point[:-1])}
    assert push_notify.key_defect(truncated) == "p256dh_malformed_64b"


def test_key_defect_rejects_right_length_but_off_curve() -> None:
    """Length and the 0x04 prefix alone do not make a usable point."""
    good = _sub_keys(9)
    point = base64.urlsafe_b64decode(good["p256dh"] + "=" * (-len(good["p256dh"]) % 4))
    bogus = bytes([0x04]) + bytes(64)  # x = y = 0 is not on P-256
    assert len(bogus) == len(point)
    assert push_notify.key_defect({**good, "p256dh": _b64u(bogus)}) == "p256dh_not_on_curve"


@pytest.mark.parametrize(
    "mutate, expected",
    [
        (lambda k: {**k, "auth": _b64u(bytes(15))}, "auth_malformed_15b"),
        (lambda k: {**k, "p256dh": "!!!not base64!!!"}, "p256dh_not_base64url"),
        (lambda k: {**k, "p256dh": ""}, "keys_missing"),
        (lambda k: "not-an-object", "keys_not_object"),
    ],
)
def test_key_defect_names_each_defect(mutate, expected: str) -> None:
    assert push_notify.key_defect(mutate(_sub_keys(10))) == expected


def test_subscribe_refuses_an_undeliverable_row(store: Path) -> None:
    """Better no row than a row that looks like an audience and never receives."""
    good = _sub_keys(11)
    point = base64.urlsafe_b64decode(good["p256dh"] + "=" * (-len(good["p256dh"]) % 4))

    client = TestClient(create_app())
    resp = client.post(
        "/push/subscribe",
        json={"endpoint": "https://push.example/sub/bad", "keys": {**good, "p256dh": _b64u(point[:-1])}},
    )
    assert resp.status_code == 400
    assert push_notify.load_subscriptions() == []


def test_dispatch_retires_an_undeliverable_row_instead_of_retrying_it(
    store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """It must land in `retired`, not `failures` — and never be sent again."""
    good = _sub_keys(12)
    point = base64.urlsafe_b64decode(good["p256dh"] + "=" * (-len(good["p256dh"]) % 4))
    store.parent.mkdir(parents=True, exist_ok=True)
    # Written past upsert's guard on purpose: the bad row predates that guard.
    store.write_text(
        json.dumps(
            {
                "endpoint": "https://push.example/sub/legacy-bad",
                "keys": {**good, "p256dh": _b64u(point[:-1])},
                "hour_kst": 22,
                "locale": "ko",
                "active": True,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        push_notify, "build_daily_metric_payload", lambda: {
            "title_ko": "t", "title_en": "t", "body_ko": "b", "body_en": "b", "url": "u",
        }
    )

    out = push_notify.dispatch_daily(force=True)
    assert out["sent"] == 0
    assert out["errors"] == 0, "an undeliverable row is not a transient failure"
    assert out["gone"] == 1
    assert out["retired"] == {"keys_p256dh_malformed_64b": 1}

    assert push_notify.load_subscriptions()[0]["active"] is False
    assert push_notify.dispatch_daily(force=True)["targets"] == 0
