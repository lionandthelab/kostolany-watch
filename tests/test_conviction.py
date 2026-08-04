"""Conviction system: vote block integrity, transcribed cells, copy guard."""

import json
import re
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from kostolany.calibration import CONFIDENCE_VIEW_BY_SYMBOL, calibration_payload
from kostolany.momo import RULE_IDS, MomoFloorHead

ROOT = Path(__file__).resolve().parents[1]


def _px(n=900, seed=4):
    rng = np.random.default_rng(seed)
    t = np.arange(n)
    ret = 0.0012 * np.sin(2 * np.pi * t / 130) + rng.normal(0, 0.01, n)
    return pd.Series(100 * np.exp(np.cumsum(ret)), index=pd.bdate_range("2019-01-02", periods=n))


def test_rule_votes_eight_columns_sum_matches_counts():
    px = _px()
    head = MomoFloorHead().fit(px)
    rv = head.rule_votes(px)
    assert list(rv.columns) == list(RULE_IDS)
    pd.testing.assert_series_equal(
        rv.sum(axis=1).rename("votes_up"), head.vote_counts(px)
    )


def test_tie_resolves_up():
    px = _px()
    head = MomoFloorHead().fit(px)
    counts = head.vote_counts(px)
    regimes, _ = head.predict(px)
    ties = counts[counts == 4].index
    if len(ties):
        assert (regimes.reindex(ties) < 3).all()


def test_vote_block_side_matches_served_regime_both_paths():
    from kostolany.engine import KostolanyEngine, fit_analyst_bundle

    # Path 1: single-model engine
    eng = KostolanyEngine(model_kind="momo")
    eng.fit_synthetic(n=900, seed=3)
    snap = eng.snapshot()
    assert snap.vote is not None
    called_up = snap.regime.startswith("A")
    assert (snap.vote["side"] == "up") == called_up
    assert len(snap.vote["rules"]) == 8
    assert snap.vote["up"] + snap.vote["down"] == 8

    # Path 2: analyst bundle
    bundle = fit_analyst_bundle("SYNTH")
    msnap = bundle["momo"].snapshot()
    assert msnap.vote is not None
    assert (msnap.vote["side"] == "up") == msnap.regime.startswith("A")
    # AI heads never carry a vote block
    for kind in ("hmm", "gbm", "tsfm"):
        assert bundle[kind].snapshot().vote is None


def test_confidence_view_cells_match_artifact_when_present():
    art = ROOT / "artifacts" / "experiments" / "confidence_menu_20260730.json"
    if not art.exists():
        pytest.skip("artifact not present (dockerignored image)")
    menu = json.loads(art.read_text(encoding="utf-8"))
    tier_map = {"unanimous": "만장일치(8-0)", "strong": "7-1", "lean": "6-2", "mixed": "분열(5-3/4-4)"}
    for sym in ("^GSPC", "BTC-USD"):
        cv = CONFIDENCE_VIEW_BY_SYMBOL[sym]
        src = menu[sym]
        for key, src_key in tier_map.items():
            assert cv["tiers"][key]["side_hit"] == pytest.approx(src["by_vote_margin"][src_key]["side"])
            assert cv["tiers"][key]["share"] == pytest.approx(src["by_vote_margin"][src_key]["share"])
        assert cv["menu"]["side_hit"] == pytest.approx(src["menu"]["side(상/하 레그)"])
        assert cv["menu"]["zone1_hit"] == pytest.approx(src["menu"]["adjacent(±1 sector arc)"])
        assert cv["menu"]["zone2_hit"] == pytest.approx(src["menu"]["within±2(5 sectors)"])
        assert cv["menu"]["exact_hit"] == pytest.approx(src["menu"]["exact6"])


def test_unmeasured_symbol_has_no_confidence_view():
    assert calibration_payload("EEM") is None
    for sym in ("^GSPC", "BTC-USD"):
        assert "confidence_view" in calibration_payload(sym)


FORBIDDEN_PATTERNS = [
    r"확신도",            # scalar-confidence vocabulary is retired from conviction copy
    r"있을 확률",          # future-probability phrasing (K7)
    r"들어올 확률",
    r"도달할 확률",
    r"권고",              # advisory vocabulary
    r"반원",
    r"동전 던지",
    r"\d+%",             # hardcoded percentages — all numbers arrive via slots
]


def _flatten_strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _flatten_strings(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _flatten_strings(v)


#: A line may opt out only by naming the spec clause that permits it, so every
#: exception is visible in review instead of invisible by scope.
ALLOW_RE = re.compile(r"spec-ok\(§[^)]+\)")

WEB_SRC = ROOT / "web" / "src"
HANGUL = re.compile(r"[가-힣]")


def _guarded_lines(path):
    """Yield (lineno, line) for lines this guard is responsible for.

    i18n dictionaries are scanned whole — every namespace, not just
    `conviction:`. A merge or refactor is a copy-relocation event, and the old
    brace-matched scope meant any string leaving that one object silently left
    the guarded region. `.tsx` files are scanned on their Korean-bearing lines,
    which is where hardcoded UI copy hides (style objects and class names are
    not copy and would only produce noise).

    An exemption must sit on the offending line itself. A preceding-comment
    window would silently widen with reformatting; same-line is unambiguous and
    shows up directly in review next to the string it excuses.
    """
    is_i18n = path.parent.name == "i18n"
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if ALLOW_RE.search(line):
            continue
        if is_i18n or HANGUL.search(line):
            yield n, line


#: User-facing copy also lives outside .ts/.tsx — the prerendered route shells
#: are the only thing a non-rendering crawler reads, and guide articles are
#: published as standalone HTML pages. Both are copy and both are guarded.
EXTRA_COPY_FILES = (
    ROOT / "web" / "scripts" / "prerender-routes.mjs",
    ROOT / "web" / "src" / "guide" / "articles.json",
)


def test_forbidden_copy_repo_wide():
    """No forbidden sentence pattern anywhere a user can read it (§6).

    Replaces the old conviction-namespace-only guard, which could see roughly
    one object of the surface it was supposed to police — 「권고」 shipped in
    `watch:` and a hardcoded `%` shipped in `models:` while it passed green.
    """
    offenders: list[str] = []
    targets = (
        sorted(WEB_SRC.rglob("*.ts"))
        + sorted(WEB_SRC.rglob("*.tsx"))
        + [p for p in EXTRA_COPY_FILES if p.exists()]
    )
    for path in targets:
        for n, line in _guarded_lines(path):
            for pat in FORBIDDEN_PATTERNS:
                if re.search(pat, line):
                    rel = path.relative_to(WEB_SRC)
                    offenders.append(f"{rel}:{n} [{pat}] {line.strip()[:100]}")
    assert not offenders, (
        "forbidden copy patterns (§6) — fix the copy, or annotate the line with "
        "`spec-ok(§N): reason` if a clause genuinely permits it:\n" + "\n".join(offenders)
    )


def test_guard_actually_covers_the_whole_surface():
    """The guard's own scope is the thing that failed last time — pin it."""
    scanned = {
        p.relative_to(WEB_SRC).as_posix()
        for p in (sorted(WEB_SRC.rglob("*.ts")) + sorted(WEB_SRC.rglob("*.tsx")))
    }
    for required in ("i18n/ko.ts", "i18n/en.ts", "WatchApp.tsx", "MacroDesk.tsx", "NewsDesk.tsx"):
        assert required in scanned, f"{required} escaped the copy guard"

    # And the allowlist must not be usable as a blanket switch.
    ko = (WEB_SRC / "i18n" / "ko.ts").read_text(encoding="utf-8")
    assert len(ALLOW_RE.findall(ko)) <= 3, "too many spec-ok exemptions — triage the copy instead"


def test_guard_detects_planted_violations(tmp_path):
    """A guard that cannot fail is theatre — that was the defect being fixed.

    Plants each forbidden pattern in both a `.tsx` and an i18n file and asserts
    the scanner reports it, then asserts the same-line exemption silences it.
    """
    i18n = tmp_path / "i18n"
    i18n.mkdir()
    samples = {
        "확신도": '  band: "확신도 높음",',
        "있을 확률": '  z: "A2에 있을 확률",',
        "권고": '  a: "권고 행동",',
        "반원": '  e: "상승 반원",',
        "동전 던지": '  m: "동전 던지기와 같습니다",',
        r"\d+%": '  p: "실측 80%",',
    }
    for pat, line in samples.items():
        ko = i18n / "ko.ts"
        ko.write_text(f"export const ko = {{\n{line}\n}};\n", encoding="utf-8")
        hits = [ln for _, ln in _guarded_lines(ko) if re.search(pat, ln)]
        assert hits, f"guard missed planted {pat!r}"

        ko.write_text(
            f"export const ko = {{\n{line} // spec-ok(§9.9): planted\n}};\n", encoding="utf-8"
        )
        hits = [ln for _, ln in _guarded_lines(ko) if re.search(pat, ln)]
        assert not hits, f"same-line exemption failed for {pat!r}"

    # .tsx is scanned on Korean-bearing lines, not just i18n dictionaries.
    tsx = tmp_path / "Bad.tsx"
    tsx.write_text('const x = <p>상승 확률 {Math.round(p * 100)}%</p>;\n', encoding="utf-8")
    assert [ln for _, ln in _guarded_lines(tsx) if re.search(r"\d+%", ln) or "확률" in ln]

    # A style object with a percent width is not copy and must not be flagged.
    clean = tmp_path / "Clean.tsx"
    clean.write_text('const s = { width: "100%" };\n', encoding="utf-8")
    assert not list(_guarded_lines(clean))
