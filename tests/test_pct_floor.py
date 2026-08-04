"""Every displayed percentage floors — spec §0.3, forbidden pattern §6 #9.

Rounding up is the named failure (81% from 0.8062). The frontend `pctFloor`
(eggGeometry.ts) and the server `pct_floor` (calibration.py) must agree, or the
same measured quantity renders twice with different values on one screen.
"""

from __future__ import annotations

import math
import re
from pathlib import Path

import pytest

from kostolany.calibration import (
    CONFIDENCE_VIEW_BY_SYMBOL,
    MEASURED_BY_SYMBOL,
    calibration_payload,
    pct_floor,
)

WEB_SRC = Path(__file__).resolve().parents[1] / "web" / "src"


def test_pct_floor_never_rounds_up():
    # The exact values §6 #9 names as forbidden renderings.
    assert pct_floor(0.8062) == 80
    assert pct_floor(0.6748) == 67
    assert pct_floor(0.8771) == 87
    assert pct_floor(0.245) == 24
    # And the two that were contradicting themselves on screen.
    assert pct_floor(0.717) == 71
    assert pct_floor(0.2589) == 25


@pytest.mark.parametrize("symbol", sorted(CONFIDENCE_VIEW_BY_SYMBOL))
def test_every_confidence_view_cell_floors(symbol):
    view = CONFIDENCE_VIEW_BY_SYMBOL[symbol]
    cells = [*view["menu"].values()]
    for tier in view["tiers"].values():
        cells += [v for v in tier.values() if isinstance(v, (int, float))]
    for value in cells:
        assert pct_floor(value) == math.floor(value * 100)


@pytest.mark.parametrize("symbol", sorted(MEASURED_BY_SYMBOL))
def test_note_uses_floored_side_hit(symbol):
    """The note and the ladder read the same source; they must print the same."""
    payload = calibration_payload(symbol)
    assert payload is not None
    note = payload["note_ko"]

    momo = MEASURED_BY_SYMBOL[symbol]["momo_floor"]
    side = momo.get("vote_side", momo.get("side_median", 0))
    assert f"{pct_floor(side)}%" in note
    # The rounded-up twin must not appear anywhere in the note.
    rounded = round(side * 100)
    if rounded != pct_floor(side):
        assert f"{rounded}%" not in note


@pytest.mark.parametrize("symbol", sorted(MEASURED_BY_SYMBOL))
def test_note_states_random_baseline_as_one_in_six(symbol):
    """§6 #6: the random baseline is 「6분의 1」 only — never 「무작위 17%」."""
    note = calibration_payload(symbol)["note_ko"]
    assert "6분의 1" in note
    assert "무작위 17" not in note
    assert "무작위 16" not in note


@pytest.mark.parametrize("symbol", sorted(MEASURED_BY_SYMBOL))
def test_note_keeps_leg_word_beside_side_percent(symbol):
    """§6 #15: a side % may not sit apart from a 레그/방향 word."""
    note = calibration_payload(symbol)["note_ko"]
    momo = MEASURED_BY_SYMBOL[symbol]["momo_floor"]
    side = momo.get("vote_side", momo.get("side_median", 0))
    idx = note.index(f"{pct_floor(side)}%")
    phrase = note[max(0, idx - 40) : idx]
    assert "레그" in phrase or "방향" in phrase


def test_no_rounding_helper_near_percent_in_tsx():
    """Static guard: `.toFixed(0)%` / `Math.round(x*100)%` must not come back.

    These are how the 71/72 and 25/26 contradictions were introduced. The only
    sanctioned path to a percent literal is pctFloor / pctFloor1.
    """
    offenders: list[str] = []
    banned = re.compile(
        r"(Math\.round\([^)]*\*\s*100\)|\*\s*100\s*\)\s*\.toFixed\(0\))\s*(\}|\)*\s*)%"
    )
    for path in sorted(WEB_SRC.rglob("*.tsx")) + sorted(WEB_SRC.rglob("*.ts")):
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if banned.search(line):
                offenders.append(f"{path.relative_to(WEB_SRC)}:{n}: {line.strip()}")
    assert not offenders, "use pctFloor for displayed percentages:\n" + "\n".join(offenders)
