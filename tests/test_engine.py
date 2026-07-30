"""Engine smoke tests."""

from kostolany import api, watch_cache
from kostolany.engine import KostolanyEngine
from kostolany.regimes import Regime


def test_synthetic_snapshot():
    eng = KostolanyEngine(model_kind="hmm")
    eng.fit_synthetic(n=800, seed=3)
    snap = eng.snapshot()
    assert snap.regime in Regime.__members__
    assert abs(sum(snap.probabilities.values()) - 1.0) < 1e-5
    assert set(snap.gauges) == {"volume", "participation", "money", "sentiment"}
    assert -1.5 <= snap.egg["x"] <= 1.5
    assert snap.disclaimer


def test_watch_rebuild_queue_preserves_requested_cache_shape():
    # Must use a symbol that is actually in WATCH_MARKETS: priority routing is
    # membership-based, and KS11 was retired from the watch surface.
    symbol = api.WATCH_MARKETS[0]
    with api._watch_job_lock:
        old_running = api._watch_worker_running
        api._watch_worker_running = True
        api._watch_priority_q.clear()
        api._watch_normal_q.clear()
        api._watch_queued.clear()
    try:
        started = api._schedule_watch_rebuild(
            symbol,
            ["hmm"],
            123,
            3,
        )
        expected = (symbol, ("hmm",), 123, 3)
        assert started
        assert expected in api._watch_queued
        assert api._watch_priority_q[0] == expected
    finally:
        with api._watch_job_lock:
            api._watch_priority_q.clear()
            api._watch_normal_q.clear()
            api._watch_queued.clear()
            api._watch_worker_running = old_running


def test_single_watch_model_does_not_fit_full_bundle(monkeypatch):
    monkeypatch.setattr(
        api,
        "fit_analyst_bundle",
        lambda _symbol: (_ for _ in ()).throw(
            AssertionError("bundle should not be fitted")
        ),
    )
    monkeypatch.setattr(
        api,
        "_build_one_analyst",
        lambda symbol, mid, limit, stride: {
            "id": mid,
            "symbol": symbol,
            "limit": limit,
            "stride": stride,
        },
    )
    body = api._build_watch_body("SYNTH", ["hmm"], 100, 2)
    assert [row["id"] for row in body["analysts"]] == ["hmm"]


def test_watch_queue_has_hard_capacity():
    with api._watch_job_lock:
        old_running = api._watch_worker_running
        api._watch_worker_running = True
        api._watch_priority_q.clear()
        api._watch_normal_q.clear()
        api._watch_queued.clear()
    try:
        for i in range(api.MAX_WATCH_QUEUE):
            assert api._enqueue_watch(
                f"CUSTOM-{i}",
                priority=False,
                ids=["hmm"],
                limit=100 + i,
                stride=2,
            )
        assert not api._enqueue_watch(
            "OVERFLOW",
            priority=False,
            ids=["hmm"],
            limit=999,
            stride=2,
        )
        assert len(api._watch_queued) == api.MAX_WATCH_QUEUE
    finally:
        with api._watch_job_lock:
            api._watch_priority_q.clear()
            api._watch_normal_q.clear()
            api._watch_queued.clear()
            api._watch_worker_running = old_running


def test_symbol_refresh_cooldown_is_shape_independent(tmp_path, monkeypatch):
    path = tmp_path / "symbol-refresh.json"
    monkeypatch.setattr(
        watch_cache,
        "_symbol_refresh_path",
        lambda _symbol: path,
    )
    watch_cache.mark_symbol_refresh_started("KS11")
    assert watch_cache.symbol_refresh_cooldown_remaining("KS11") > 3500
