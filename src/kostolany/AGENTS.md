<!-- Parent: ../AGENTS.md -->

# src/kostolany

## Purpose
Python package: data → features → labels → models → engine → API/CLI. Evaluation tooling lives in `harness/`.

## Key files

| File | Role |
|---|---|
| `regimes.py` | 6-regime taxonomy, colors, egg coords, disclaimer |
| `features.py` | Causal feature matrix + gauges |
| `labels.py` | Weak (train) / gold (eval-only) |
| `models.py` | HMM, LightGBM, ensemble |
| `engine.py` | Fit + snapshot assembly |
| `data.py` | Yahoo + synthetic DGP |
| `api.py` / `cli.py` | Serving & demo |

## For agents

- Prefer skill `kostolany-implement` then `kostolany-verify`
- Domain edits → `kostolany-regime`
- Nested guide: `harness/AGENTS.md`
