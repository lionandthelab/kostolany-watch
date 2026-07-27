"""Run AI forecast/regime tuning experiment and print scorecard.

Usage:
  .\\.venv\\Scripts\\python.exe scripts/run_forecast_experiment.py
  .\\.venv\\Scripts\\python.exe scripts/run_forecast_experiment.py --symbol SPY
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kostolany.harness.forecast_tune import run_experiment, summarize  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", default="KS11")
    p.add_argument("--start", default="2010-01-01")
    p.add_argument("--splits", type=int, default=6)
    args = p.parse_args()

    print(f"== Experiment {args.symbol} ==")
    results = run_experiment(args.symbol, start=args.start, n_splits=args.splits)
    text = summarize(results)
    md_path = Path(results["artifact"]).with_suffix(".md")
    md_path.write_text(text, encoding="utf-8")
    # Avoid Windows cp949 console crashes on unicode
    sys.stdout.buffer.write((text + "\n").encode("utf-8", errors="replace"))
    print(f"\nSaved: {results['artifact']}")
    print(f"Report: {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
