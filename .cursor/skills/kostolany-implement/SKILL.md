---
name: kostolany-implement
description: Implement Kostolany Watch features with Cursor agent harness routing, leakage-safe ML, and verify-before-done. Use when building engine/harness/API/web features, extending regimes, or when the user asks to implement something in this repo.
---

# Kostolany implement

## Before coding

1. Read root `AGENTS.md` and the nearest nested `AGENTS.md` if present.
2. Classify the change:
   - **harness/eval** → also load skill `kostolany-verify` at the end; obey `.cursor/rules/leakage-safe-ml.mdc`
   - **domain/regimes/labels** → read skill `kostolany-regime`
   - **web** → obey `.cursor/rules/web-egg-ux.mdc`
3. If scope is multi-file / multi-lane, prefer parallel Task executors (`ultrawork`) after a short plan.
4. If requirements are ambiguous, stop and clarify (or deep-interview) before large edits.

## Implementation order

1. Data/features/labels (causal only)
2. Model or harness change
3. Tests under `tests/`
4. API/CLI wiring if user-facing
5. Web only if UX is in scope
6. **Always** finish with `kostolany-verify`

## Hard constraints

- Never train on gold/planted labels
- Keep disclaimer on user-facing outputs
- No random CV on time series
- Prefer small diffs; do not rewrite unrelated ML eval code unless asked

## Done means

- `python scripts/agent_verify.py` exits 0
- Behavior change covered by a test or an explicit manual check note
