"""Run pooled cross-asset forecast research evaluation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kostolany.harness.pooled_forecast import (  # noqa: E402
    DEFAULT_PANEL,
    evaluate_pooled_forecast,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--targets", default="KS11,^GSPC,BTC-USD")
    parser.add_argument("--panel", default=",".join(DEFAULT_PANEL))
    parser.add_argument("--origin-stride", type=int, default=63)
    parser.add_argument("--refit-days", type=int, default=730)
    args = parser.parse_args()
    result = evaluate_pooled_forecast(
        [x.strip() for x in args.targets.split(",") if x.strip()],
        panel_symbols=[x.strip() for x in args.panel.split(",") if x.strip()],
        origin_stride=args.origin_stride,
        refit_days=args.refit_days,
    )
    sys.stdout.buffer.write(
        (json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
