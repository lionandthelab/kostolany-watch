# Briefing pipeline

Educational only — not investment advice.

## Two tracks

| Track | Who writes | Where it runs if PC is off? | Email |
|---|---|---|---|
| **Daily card** | Deterministic from `/watch` cache + `/news` | **Yes** — Cloud Scheduler → Cloud Run | Resend |
| **Weekly brief** | **Claude Code on this PC** (subscription) | **No** — needs PC awake | Resend after POST |

## Daily (cloud)

```powershell
.\scripts\setup-newsletter-scheduler.ps1
```

Job `newsletter-daily-generate` hits  
`POST /api/briefs/daily/generate?dispatch=true` daily 22:00 KST.

## Weekly (local Claude)

1. Install CLI (once): `npm i -g @anthropic-ai/claude-code` then `claude` to log in.
2. Ensure `.env` has `NEWSLETTER_CRON_SECRET` + `RESEND_*` (same as Cloud Run).
3. Dry-run:

   ```powershell
   .\.venv\Scripts\python.exe scripts\generate_weekly_brief.py --dry-run
   ```

4. Register Friday 09:00 local task:

   ```powershell
   .\scripts\register-weekly-brief-task.ps1
   ```

Flow: fetch API context → Claude writes KO/EN HTML JSON → `POST /api/briefs` (stores GCS) → optional email dispatch → Guide SPA lists via `GET /api/briefs`.

## API

- `GET /api/briefs` / `GET /api/briefs/{slug}` — public read
- `POST /api/briefs` — publish (header `X-Cron-Secret`)
- `POST /api/briefs/daily/generate` — cloud daily
- `POST /api/newsletter/dispatch?kind=weekly|daily` — email only
