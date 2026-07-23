<!-- Parent: ../AGENTS.md -->

# harness

## Purpose
Leakage-aware evaluation: purged CV, auditor, metrics, economic backtest, experiment runner.

## Non-negotiables

- Gold labels only via eval paths (`y_gold`, `evaluate_regimes`)
- `execution_lag >= 1`
- Time-series CV only (`CombinatorialPurgedCV` / `PurgedWalkForward`)

## Test

```bash
python -m pytest tests/test_harness.py -q
python scripts/agent_verify.py
```
