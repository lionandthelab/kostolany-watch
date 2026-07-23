"""Engine smoke tests."""

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
