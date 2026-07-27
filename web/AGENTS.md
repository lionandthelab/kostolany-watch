<!-- Parent: ../AGENTS.md -->

# web

## Purpose
Egg-first Vite/React UI. Proxies `/api` to FastAPI.

## IA (1 depth)

| View | How |
|---|---|
| Landing | `/` |
| Watch | `#watch` |
| News desk | `#news` |
| Sector flows | `#flows` |

Top desk tabs: 국면 ↔ 뉴스 ↔ 흐름 (same app shell).

## For agents

- Obey `.cursor/rules/web-egg-ux.mdc`
- CTA must enter watch in one click; symbol/model changes auto-fetch
- Dev needs API: `kostolany serve` on :8000
