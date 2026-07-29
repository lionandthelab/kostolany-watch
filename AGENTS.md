# Kostolany Watch — Agent Guide

교육·연구용 코스톨라니 6국면(A1–A3, B1–B3) 확률 엔진 + 웹 UI.

## Agent harness (Cursor)

이 레포의 기본 작업 방식은 **Cursor agent harness**다. 구현 전에 아래를 읽고 따른다.

| Layer | Path | Role |
|---|---|---|
| Rules | `.cursor/rules/*.mdc` | 항상/파일별 가드레일 |
| Skills | `.cursor/skills/*/SKILL.md` | 워크플로 절차 |
| Hooks | `.cursor/hooks.json` | stop 검증·위험 셸 게이트 |
| Verify | `scripts/agent_verify.py` | pytest + leakage smoke |

### 권장 라우팅

- 모호한 요구 → `/deep-interview` 또는 planner
- 병렬 구현 → `/ulw` (ultrawork) + executor lanes
- 완료까지 반복 → `/ralph` (verify 루프 포함)
- 전체 자율 → `/autopilot`
- 이 레포 작업 기본 → skill `kostolany-implement`, 끝에는 반드시 `kostolany-verify`

### Non-negotiables

1. **Gold / planted labels는 평가 전용** — 학습·`fit_predict` 입력 금지
2. **execution_lag ≥ 1** — 신호 봉 동시 체결 금지
3. **면책 문구** — 사용자 대면 출력에 상시 유지 (투자 권유 아님)
4. **검증 없이 done 선언 금지** — `python scripts/agent_verify.py` 통과 필요

## Layout

```
src/kostolany/
  harness/   # purged CV, leakage, metrics, backtest, runner
  features.py labels.py models.py engine.py data.py api.py cli.py
web/         # Vite + React egg UI
tests/       # harness + engine
scripts/     # agent verify helpers
.cursor/     # rules, skills, hooks
```

## Commands

```bash
# venv
.\.venv\Scripts\activate   # Windows
pip install -e ".[dev]"
# optional Korea connectors
pip install -e ".[korea]"

# agent verify (hooks/skills가 호출)
python scripts/agent_verify.py

# product
kostolany demo
kostolany fetch-data --symbol KS11
kostolany serve
cd web && npm run dev
```

## Data connectors

- **FRED** (`FRED_API_KEY` in `.env`) → money/credit/VIX extras; without key falls back to Yahoo (^VIX/^IRX/^TNX)
- **KRX** → `KS11` via FinanceDataReader/pykrx when installed (`pip install -e ".[korea]"`), else Yahoo `^KS11`
- Cache: `artifacts/cache/`

## TSFM v3

- Default: causal local PatchTST-lite trajectory head (`LocalTSFM`)
- Ensemble: HMM 0.28 + GBM 0.52 + direct TSFM 0.20 (`model=tsfm|ensemble_v3`)
- The Chronos backend and the `[tsfm]` extra were removed; `build_tsfm()` always
  returns `LocalTSFM`. There is no `KOSTOLANY_TSFM_BACKEND` setting any more.

## Domain map (short)

- Axes: volume, participation
- Drivers: money, sentiment
- Position: trend/drawdown
- Output: 6-regime proba + gauges + egg (x,y) + action + disclaimer

자세한 기획: `kostolany_engine_proposal.md`  
평가 하네스(코드): `docs/HARNESS.md`  
Agent 운용: `docs/AGENT_HARNESS.md`
