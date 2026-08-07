# Point-in-time ledger

Educational only — not investment advice.

Append-only archive of what the desk actually showed, one record per **KST date**,
written once and never overwritten.

## Why

Every backtest this repo can run *later* is contaminated:

- `data.py` loads prices with `auto_adjust=True` — a 2015 bar sees the 2026
  back-adjusted close (`research/sota_design.md` §5, restatement canary).
- FRED restates its own history. Measured publication lags: M2SL +57d,
  CPIAUCSL +43d, FEDFUNDS +30d.
- Google News RSS is a rolling window. Korean headlines not captured on the day
  are gone.

A record written today is not contaminated, and **cannot be reconstructed after
the fact by anyone, including us**. Elapsed wall-clock time is the only
non-replicable input available to this project — which is why the job starts
before there is anything to use it for.

## What a record holds

`GET /api/ledger/2026-08-04`

| Section | Contents |
|---|---|
| `calls` | Every head's served call (`momo`/`hmm`/`gbm`/`tsfm` × both markets): regime, probabilities, gauges, the live 8-rule `vote`, the `confidence_view` table on screen, `payload_version`, and **`cache_age_hours`** — how stale the displayed call was when archived |
| `macro` | Each board series as `{value, unit, observed_at}`. `observed_at` is the vintage stamp: when FRED restates the June print, tomorrow's row carries the same `observed_at` with a different `value`, and the restatement becomes a visible diff instead of a silent rewrite |
| `news` | Up to 48 headlines with `published_at`, source, theme, url |
| `errors` | Per-section failure strings. Gaps are recorded, never smoothed over — "the desk showed nothing for news that day" is itself a fact |
| `content_sha256` | SHA-256 over canonical (sorted, compact) JSON of the record minus this field. File formatting is irrelevant; a re-serialised copy verifies identically |

All four heads are archived, not just the shipped `momo` one — scoring a head we
did **not** display is the only way to answer "would the other arm have done
better" without re-running history through revised data.

## Immutability

`save_record()` takes no `force` parameter and there is no overwrite path. A
ledger that can be rewritten once the outcome is known is not evidence of
anything, and this repo has a standing rule against making a failed measurement
pass by editing it (`AGENTS.md` non-negotiables, item 3).

Re-running the job is therefore safe and idempotent: an already-recorded day
returns `{"status": "already_recorded"}` and touches nothing.

If every section fails, the day is **not** written (`status: "skipped_empty"`) so
the scheduler can retry rather than burning the single write slot on an empty
row. A *partial* capture does commit, with its gaps in `errors`.

## Storage

| | Path |
|---|---|
| Local | `artifacts/ledger/day/{YYYY-MM-DD}.json`, `artifacts/ledger/index/{YYYY-MM}.json` |
| GCS | `gs://$GCS_CACHE_BUCKET/ledger/day/…`, `…/ledger/index/…` |

GCS is the archive of record — Cloud Run instances are ephemeral, so
`GCS_CACHE_BUCKET` **must** be set or the ledger dies with the instance. The
setup script refuses to install the job without it.

Size: ~36 KB/day ≈ 13 MB/year.

## Schedule

```powershell
.\scripts\setup-ledger-scheduler.ps1
```

Job `ledger-daily-record` hits `POST /api/ledger/record` at **23:50 KST**
(14:50 UTC) — end of the Seoul day, after the 22:00 KST daily card. Runs in the
cloud; the PC being off is irrelevant.

Note the day boundary: at 23:50 KST the US session for that calendar date has
not closed yet (it closes ~05:00 KST the next morning), so the row carries the
previous US close. That is correct — the ledger records **what the desk
displayed on KST date D**, and every series carries its own `asof`/`observed_at`.

## API

- `POST /api/ledger/record` — archive today (header `X-Cron-Secret`); optional `?date=YYYY-MM-DD`
- `GET /api/ledger?month=YYYY-MM` — index rows, newest first
- `GET /api/ledger/{YYYY-MM-DD}` — one full record

## Not built yet

- **Scoring.** Records accumulate now; scoring a call against realised gold
  labels needs the leg to close (~76 bars — 2,816 OOS bars / 37 legs on ^GSPC,
  `calibration.MEASURED_BY_SYMBOL`). Do not build the public scorecard until
  there is enough archived history to score, and pre-register the scoring rule
  before looking at results.

  **Status 2026-08-07: the rule is pre-registered** —
  [`LEDGER_SCORING_PREREG_2026-08-07.md`](LEDGER_SCORING_PREREG_2026-08-07.md).
  Fixed while zero legs had closed, so post-hoc selection is structurally
  impossible rather than merely discouraged. The scorer itself is still
  unwritten; it must not run against the real ledger before the tier-1
  threshold (8 pooled legs, estimated 2027 Q3) and must not be *judged* before
  tier 2 (20 pooled legs, estimated 2029 Q1). Both estimates, and the arithmetic
  behind them, are in §4 of that document.

  Ingestion continuity is a precondition of scoring validity — a ledger that
  stops silently makes the whole asset worthless, and outage days are unlikely
  to be missing at random. §7 of the prereg fixes the handling: missing bars are
  **excluded, never interpolated**, coverage is measured over distinct `asof`
  bars (not calendar days), and a market whose bar coverage falls below 0.90 is
  reported as unjudged. `GET /api/health/freshness` is the early-warning
  surface, but a green light there is not evidence of validity — the scorer
  recomputes coverage itself.
- **Retention hardening.** GCS retention policies are bucket-level, so an
  immutable-by-infrastructure ledger needs its own bucket. Today immutability is
  enforced in code only.
