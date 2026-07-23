<!-- Parent: ../AGENTS.md -->

# web

## Purpose
Egg-first Vite/React UI. Proxies `/api` to FastAPI.

## IA (1 depth)

| View | How |
|---|---|
| Landing | `/` |
| Watch | same page via CTA → `#watch` (no route stack) |

## For agents

- Obey `.cursor/rules/web-egg-ux.mdc`
- CTA must enter watch in one click; symbol/model changes auto-fetch
- Dev needs API: `kostolany serve` on :8000
