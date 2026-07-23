---
name: kostolany-regime
description: Kostolany 6-regime domain mapping (A1–A3, B1–B3), feature axes, weak vs gold labels, and action framing. Use when editing labels, regimes, gauges, egg coordinates, recommendations, or explaining regime logic.
---

# Kostolany regime domain

## Six regimes

| Code | Meaning | Typical action (non-advice) |
|---|---|---|
| A1 | Accumulation | 매수·축적 |
| A2 | Participation up | 보유·관망 |
| A3 | Euphoria | 매도·차익 |
| B1 | Distribution | 매도·정리 |
| B2 | Decline | 관망·현금 |
| B3 | Capitulation | 매수 |

Cycle prior: A1→A2→A3→B1→B2→B3→A1

## Feature groups

- **volume** / **participation** (axes)
- **money** / **sentiment** (drivers)
- **position** (trend, drawdown, MA gap)

Gauges for UX come from `gauge_scores()`; egg point from probability-weighted `REGIME_META` coords.

## Labeling policy

1. **Weak** (`weak_labels`) — causal rules/scores → training OK
2. **HMM map** — unsupervised states → regime prototypes → training OK
3. **Gold** (`gold_labels`) — post-hoc peaks/troughs → **eval only**
4. Synthetic planted regimes → treat like gold for scoring; demo may use noisy copies as weak

## Framing

Outputs are probabilistic regime recognition aids. Never promise tops/bottoms or guaranteed PnL.
