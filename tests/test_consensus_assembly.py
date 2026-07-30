"""assemble_paths: the shipped consensus arithmetic must be pure and scoreable."""

import numpy as np

from kostolany.flows import ArmPath, arm_from_trajectory, assemble_paths


def _proba(**over: float) -> dict[str, float]:
    p = {"A1": 1 / 6, "A2": 1 / 6, "A3": 1 / 6, "B1": 1 / 6, "B2": 1 / 6, "B3": 1 / 6}
    p.update(over)
    s = sum(p.values())
    return {k: v / s for k, v in p.items()}


def _dates(n: int = 63) -> list[str]:
    return [f"h{i}" for i in range(1, n + 1)]


def _learned(points_end: float = 104.0) -> ArmPath:
    dates = _dates()
    step = (points_end / 100.0) ** (1 / len(dates))
    pts = []
    lvl = 100.0
    for d in dates:
        lvl *= step
        pts.append({"date": d, "value": round(lvl, 6)})
    return ArmPath(points=pts, engine="local_tsfm", kind="learned", band=None, p_up=0.61)


def test_assemble_is_pure_and_consensus_is_mean_of_ends():
    probas = {"hmm": _proba(A2=0.5), "gbm": _proba(B2=0.5), "tsfm": _proba()}
    a1 = assemble_paths(probas, _dates(), _learned())
    a2 = assemble_paths(probas, _dates(), _learned())
    assert a1["ends"] == a2["ends"]  # deterministic — no live RNG in the path
    expect = float(np.mean([a1["ends"][k] for k in ("hmm", "gbm", "tsfm")]))
    assert abs(a1["consensus_level"] - expect) < 1e-9
    assert a1["forecast_engine"] == "local_tsfm"
    assert a1["consensus_outlook"] in ("up", "down")


def test_prior_arms_are_closed_form_of_proba_only():
    # Same probas -> identical prior arm paths, regardless of the learned arm.
    probas = {"hmm": _proba(A2=0.7), "gbm": _proba(A2=0.7), "tsfm": _proba()}
    a = assemble_paths(probas, _dates(), _learned(104.0))
    b = assemble_paths(probas, _dates(), _learned(88.0))
    assert a["arms"]["hmm"].points == b["arms"]["hmm"].points
    assert a["arms"]["gbm"].points == b["arms"]["gbm"].points
    # The learned arm end moved; the consensus must move by exactly 1/3 of it.
    delta_learned = a["ends"]["tsfm"] - b["ends"]["tsfm"]
    delta_cons = a["consensus_level"] - b["consensus_level"]
    assert abs(delta_cons - delta_learned / 3.0) < 1e-9


def test_arm_from_trajectory_none_falls_back_to_prior_table():
    arm = arm_from_trajectory(None, 100.0, _dates(), _proba(A2=0.6), direct_weight=0.9)
    assert arm.engine == "regime_prior_fallback"
    assert arm.kind == "regime_prior"
    assert arm.band is None and arm.p_up is None
    # Prior table amplitude is structurally clamped (~±3.4% over 63d).
    end = float(arm.points[-1]["value"])
    assert 96.0 < end < 104.5
