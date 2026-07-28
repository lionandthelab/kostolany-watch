"""Run commercial-v4 multi-market promotion evaluation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kostolany.harness.commercial_eval import run_commercial_evaluation  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", default="KS11,^GSPC,BTC-USD")
    parser.add_argument("--start", default="2010-01-01")
    parser.add_argument("--origin-stride", type=int, default=21)
    parser.add_argument("--refit-stride", type=int, default=126)
    parser.add_argument("--skip-regime", action="store_true")
    parser.add_argument("--output-dir", default="artifacts/experiments")
    args = parser.parse_args()

    result = run_commercial_evaluation(
        [s.strip() for s in args.symbols.split(",") if s.strip()],
        start=args.start,
        output_dir=args.output_dir,
        include_regime=not args.skip_regime,
        origin_stride=args.origin_stride,
        refit_stride=args.refit_stride,
    )
    text = json.dumps(result, ensure_ascii=False, indent=2)
    sys.stdout.buffer.write((text + "\n").encode("utf-8", errors="replace"))
    return 0 if result["pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
