# Weekly regime brief — publishing routine

Cadence: **every Friday** (Asia/Seoul). Educational regime reading only — not investment advice.

## Checklist (≈30–45 min)

1. Open the live desks: `/watch` (US + crypto), `/macro`, `/news`.
2. Scaffold next draft (if missing):

   ```bash
   cd web
   npm run weekly:new
   # or: node scripts/new-weekly-brief.mjs --date YYYY-MM-DD
   ```

3. Edit `web/src/guide/articles.json` — replace `TODO` title/body (KO + EN). Same structure every week:
   - one observation paragraph
   - three-step reading order (regime → macro → news)
   - one line to keep
   - disclaimer paragraph (keep)
4. Publish flag:

   ```bash
   node scripts/new-weekly-brief.mjs --date YYYY-MM-DD --publish
   ```

5. Build static HTML + sitemap + RSS:

   ```bash
   npm run build:guide
   ```

6. Deploy hosting (and API if newsletter code changed):

   ```powershell
   .\scripts\deploy-firebase.ps1
   # or hosting only: firebase deploy --only hosting
   ```

7. Search Console: URL inspection on the new `/guide/weekly-YYYY-MM-DD/` (optional but useful early on).
8. Distribution: post one channel note + link (when you pick a channel).

## Feeds & signup

- Public RSS: `https://kostolany-watch.web.app/guide/feed.xml`
- Email signup: `POST /api/newsletter/subscribe` (Guide + Landing). Stored in `artifacts/newsletter/subscribers.jsonl` + GCS `newsletter/subscribers.jsonl`.
- Delivery: **Resend** (`RESEND_API_KEY`, optional `RESEND_FROM`). Welcome mail on subscribe; weekly dispatch reads the live RSS and emails the latest `/guide/weekly-*` item.

## Cloud schedule (no local device)

| Job | Where it runs | Depends on your PC? |
|---|---|---|
| Friday email dispatch | **Google Cloud Scheduler** → Cloud Run `POST /api/newsletter/dispatch` | **No** |
| Thursday “write the brief” reminder | GitHub Actions (`.github/workflows/weekly-brief-reminder.yml`) after it is on the **default branch** | **No** (GitHub’s cloud) |

Setup / refresh scheduler:

```powershell
# .env must include RESEND_API_KEY and (optional) NEWSLETTER_CRON_SECRET
.\scripts\setup-newsletter-scheduler.ps1
```

Cron: `0 1 * * 5` UTC = **Friday 10:00 KST**. Idempotent: same weekly slug is not re-sent unless `force=true`.

Manual dry-run:

```bash
curl -X POST "https://kostolany-watch.web.app/api/newsletter/dispatch?dry_run=true" \
  -H "X-Cron-Secret: $NEWSLETTER_CRON_SECRET"
```

## Drafts

`"status": "draft"` articles are skipped by `build-guide` and the SPA list. Only `"published"` (or omitted status) go live.
