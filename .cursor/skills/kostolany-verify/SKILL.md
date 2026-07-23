---
name: kostolany-verify
description: Run Kostolany Watch agent verification (pytest, leakage smoke, optional demo). Use when finishing a task, before claiming done, after harness/model/label changes, or when ralph/ultrawork needs a completion gate.
---

# Kostolany verify

## Command

From repo root (venv activated preferred):

```bash
python scripts/agent_verify.py
```

Optional flags:

```bash
python scripts/agent_verify.py --demo
python scripts/agent_verify.py --quick
```

## Gate checklist

- [ ] `scripts/agent_verify.py` exit code 0
- [ ] No new `GOLD_IN_TRAIN` / `ZERO_LAG` paths introduced
- [ ] If UI changed: egg-first layout still holds; disclaimer still present
- [ ] If claiming economic edge: PSR/DSR or honest negative Sharpe reported (no cherry-pick)

## Failure loop

1. Read failing test/assertion
2. Fix root cause (not assert relaxation unless test was wrong)
3. Re-run verify
4. Only then mark the parent task complete

## Ralph / stop hook

When used inside ralph or a stop-hook follow-up: treat non-zero verify as **incomplete**, continue fixing.
