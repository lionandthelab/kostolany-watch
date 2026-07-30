"""Score the SHIPPED Flows consensus, walk-forward, for the first time.

Pre-registration P-CONS-1 (docs/S0_PREREGISTRATION_2026-07-30.md, decided
before this script was ever run): amplitude_ratio ~= 0.4 and
skill_vs_trivial < 0 in at least 2 of 3 markets. If that holds, the audit
diagnosis (the constant arms clamp the shipped number) is confirmed; if not,
the audit is falsified. Either way we learn something for the first time.

Usage:
  .\\.venv\\Scripts\\python.exe scripts\\run_consensus_eval.py
  .\\.venv\\Scripts\\python.exe scripts\\run_consensus_eval.py --symbols SPY BTC-USD
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kostolany.harness.commercial_eval import evaluate_consensus_market  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", nargs="+", default=["SPY", "KS11", "BTC-USD"])
    ap.add_argument("--start", default="2010-01-01")
    ap.add_argument("--origin-stride", type=int, default=21)
    ap.add_argument("--refit-stride", type=int, default=126)
    ap.add_argument("--min-train", type=int, default=756)
    args = ap.parse_args()

    results = {}
    errors = {}
    for sym in args.symbols:
        print(f"[consensus] {sym} ...", flush=True)
        try:
            score = evaluate_consensus_market(
                sym,
                start=args.start,
                origin_stride=args.origin_stride,
                refit_stride=args.refit_stride,
                min_train=args.min_train,
            )
            results[sym] = asdict(score)
            print(
                f"  n={score.n_origins} hit={score.direction_hit:.3f} "
                f"trivial={max(score.always_up_hit, 1 - score.always_up_hit):.3f} "
                f"skill={score.skill_vs_trivial:+.3f} "
                f"amp_ratio={score.amplitude_ratio:.3f} "
                f"ci=[{score.direction_ci_low:.3f},{score.direction_ci_high:.3f}] "
                f"engine={score.learned_arm_engine} ({score.elapsed_seconds:.0f}s)",
                flush=True,
            )
        except Exception as exc:  # noqa: BLE001
            errors[sym] = str(exc)
            print(f"  FAILED: {exc}", flush=True)

    prereg = {
        "id": "P-CONS-1",
        "predicted_amplitude_ratio": 0.4,
        "predicted_negative_skill_markets": ">=2 of 3",
        "registered_in": "docs/S0_PREREGISTRATION_2026-07-30.md / AI_RND_PLAN_2026-07-29.md Phase 0",
    }
    payload = {
        "kind": "consensus_walkforward_v1",
        "asof": datetime.now(timezone.utc).isoformat(),
        "constraints": {
            "origin_stride": args.origin_stride,
            "refit_stride": args.refit_stride,
            "min_train": args.min_train,
            "learned_arm": "local_tsfm@0.90 (pooled arm not walk-forward reproducible yet — Phase 3)",
            "assembly": "kostolany.flows.assemble_paths — identical function to serving",
        },
        "preregistration": prereg,
        "markets": results,
        "errors": errors,
    }
    out_dir = ROOT / "artifacts" / "experiments"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = out_dir / f"consensus_eval_{stamp}.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nSaved: {path}")
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
