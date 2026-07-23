# Kostolany Watch

코스톨라니 달걀 6국면을 **확률적으로** 판별하는 엔진과, 금융 ML의 표준 누수 방지 기법을 담은 **SOTA 평가 하네스**.

> 교육·연구용 국면 인식 보조 도구입니다. 투자 권유·자문이 아닙니다.

## 한 줄 요약

과거 시장 데이터로 학습해 현재가 A1–A3 / B1–B3 중 어디인지 확률로 보여주고, purged CV·embargo·실행지연·누수 감사로 **정직하게** 검증합니다.

## 아키텍처

```
data → features → labels(weak/gold) → models(HMM[+GBM]) → harness(CPCV/backtest) → API → web
```

핵심은 `src/kostolany/harness/` 입니다.

| 모듈 | 역할 |
|---|---|
| `harness/cv.py` | Combinatorial Purged CV + Embargo (López de Prado) |
| `harness/leakage.py` | Look-ahead / label leakage auditor |
| `harness/metrics.py` | 국면 F1 · 전환 lead/lag · calibration |
| `harness/backtest.py` | 실행지연·거래비용 반영 경제 백테스트 |
| `harness/runner.py` | Walk-forward experiment runner |

## Agent harness (Cursor)

구현은 Cursor agent harness로 돌린다. 상세: [`docs/AGENT_HARNESS.md`](docs/AGENT_HARNESS.md) · 진입점: [`AGENTS.md`](AGENTS.md)

| Layer | Path |
|---|---|
| Rules | `.cursor/rules/` |
| Skills | `.cursor/skills/kostolany-{implement,verify,regime}/` |
| Hooks | `.cursor/hooks.json` |
| Verify gate | `python scripts/agent_verify.py` |

권장: 구현 시 skill `kostolany-implement` → 완료 시 `kostolany-verify`. 대형 작업은 `/ulw` 또는 `/ralph`.

## Evaluation harness (ML code)

모델 평가용 purged-CV / leakage auditor 는 `src/kostolany/harness/` — [`docs/HARNESS.md`](docs/HARNESS.md)

## Quickstart

```bash
py -3.11 -m venv .venv
.\.venv\Scripts\activate
pip install -e ".[dev]"

kostolany demo
kostolany fetch-data --symbol KS11   # KRX + FRED/Yahoo extras
kostolany serve
cd web && npm install && npm run dev
```

웹은 `http://127.0.0.1:5173`, API는 `http://127.0.0.1:8000` (Vite가 `/api` 프록시 → `/api/*`).

## Deploy (Firebase Hosting + Cloud Run)

```powershell
firebase login   # 토큰 만료 시 필수
.\scripts\deploy-firebase.ps1
```

상세: [`docs/DEPLOY_FIREBASE.md`](docs/DEPLOY_FIREBASE.md) · 프로젝트 id: `kostolany-watch`

- Agent harness: [`docs/AGENT_HARNESS.md`](docs/AGENT_HARNESS.md)
- ML eval harness: [`docs/HARNESS.md`](docs/HARNESS.md)
- `.env.example` → `FRED_API_KEY`, `KOSTOLANY_TSFM_BACKEND`

## 평가 원칙 (Harness)

1. **Gold labels는 평가 전용** — 추론 파이프라인에 절대 투입하지 않음
2. **Purged + Embargo CV** — 학습/검증 경계의 정보 누수 차단
3. **Execution lag ≥ 1일** — 신호 확정 후 다음 봉에서 집행
4. **Leakage auditor** — 피처·라벨 시차 위반을 자동 검출
5. **3층 메트릭** — 분류 / 전환 감지 / 경제성(Sharpe·MDD)

## 면책

본 저장소의 출력은 정보·교육 목적이며 금융투자상품에 대한 투자 권유가 아닙니다.
