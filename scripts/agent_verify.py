"""Agent verification entrypoint for Cursor hooks/skills.

Exit 0 = safe to claim done. Non-zero = incomplete.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(cmd: list[str]) -> int:
    print("+", " ".join(cmd), flush=True)
    return subprocess.call(cmd, cwd=ROOT)


def leakage_smoke() -> int:
    """Import-time + auditor smoke without full demo."""
    code = r"""
from kostolany.data import make_synthetic
from kostolany.engine import prepare_xy
from kostolany.harness.leakage import LeakageAuditor

market, planted = make_synthetic(n=400, seed=0)
X, y_weak, y_gold, prices = prepare_xy(market)
valid = X.dropna().index.intersection(y_weak.dropna().index)
X, y_weak = X.loc[valid], y_weak.loc[valid]
rep_ok = LeakageAuditor().audit(X, y_weak, gold_labels=y_gold.reindex(X.index), gold_used_in_training=False, execution_lag=1)
rep_bad = LeakageAuditor().audit(X, y_weak, gold_used_in_training=True, execution_lag=1)
assert rep_ok.passed, rep_ok.to_dict()
assert not rep_bad.passed, "auditor must fail when gold is used in training"
print("leakage_smoke: OK")
"""
    py = ROOT / ".venv" / "Scripts" / "python.exe"
    exe = str(py) if py.exists() else sys.executable
    return run([exe, "-c", code])


def pit_prefix_property() -> int:
    """G13: pit_state must be prefix-stable on 500 random (seed, cut) probes."""
    code = r"""
import numpy as np, pandas as pd
from kostolany.labels_pit import pit_state

rng = np.random.default_rng(20260730)
checked = 0
for seed in range(5):
    g = np.random.default_rng(seed)
    t = np.arange(1200)
    ret = 0.0015*np.sin(2*np.pi*t/110) + g.normal(0, 0.011, len(t))
    px = pd.Series(100*np.exp(np.cumsum(ret)), index=pd.bdate_range("2015-01-02", periods=len(t)))
    full = pit_state(px)
    for cut in rng.integers(80, len(px), size=100):
        prefix = pit_state(px.iloc[:cut])
        pd.testing.assert_frame_equal(prefix, full.iloc[:cut])
        checked += 1
assert checked == 500, checked
print(f"pit_prefix_property: OK ({checked}/500)")
"""
    py = ROOT / ".venv" / "Scripts" / "python.exe"
    exe = str(py) if py.exists() else sys.executable
    return run([exe, "-c", code])


def main() -> int:
    p = argparse.ArgumentParser(description="Kostolany agent verify gate")
    p.add_argument("--quick", action="store_true", help="Skip full pytest, leakage smoke only")
    p.add_argument("--demo", action="store_true", help="Also run kostolany demo (hmm, small n)")
    args = p.parse_args()

    py = ROOT / ".venv" / "Scripts" / "python.exe"
    exe = str(py) if py.exists() else sys.executable

    if not args.quick:
        rc = run([exe, "-m", "pytest", "-q", "--tb=line"])
        if rc != 0:
            return rc

    rc = leakage_smoke()
    if rc != 0:
        return rc

    rc = pit_prefix_property()
    if rc != 0:
        return rc

    if args.demo:
        rc = run([exe, "-m", "kostolany.cli", "demo", "--n", "800", "--model", "hmm", "--cv", "walkforward"])
        if rc != 0:
            return rc

    print("agent_verify: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
