# 고도화 실험 결과 — 3개월 방향 예측 & 국면 분류 (2026-07-27)

Kostolany Watch의 **흐름(Flows) 3개월 시나리오**와 **국면(Watch) 모델 하이퍼파라미터**를 개선하기 위해 수행한 walk-forward 실험 기록이다.  
프로덕션에 반영된 설정과, 재현 방법·한계를 한곳에서 확인할 수 있게 정리했다.

| 항목 | 값 |
|---|---|
| 심볼 | KS11 (코스피) |
| 데이터 시작 | 2010-01-01 |
| 표본 | 3,988일 · gold OOS folds=6 |
| 예측 지평 | 63거래일 (~3개월) |
| 방향평가 origins | 163개 (dense walk-forward) |
| 소요 | ~28분 (1,694.8s) |
| 하네스 | `src/kostolany/harness/forecast_tune.py` |
| 러너 | `scripts/run_forecast_experiment.py` |
| 원본 스코어카드 | `artifacts/experiments/forecast_tune_KS11_20260727T092816Z.{md,json}` (로컬, gitignore) |

---

## 1. 무엇을 측정했나

### A. 국면 분류 (gold OOS)

약한 라벨로 학습하고 **planted/gold 라벨은 평가만**에 사용 (기존 하네스 원칙과 동일).

메트릭: accuracy, macro-F1.

### B. 3개월 방향 예측

각 origin에서 모델이 만든 전방 경로의 **누적 수익률 부호**가 실현 수익률과 일치하는지(direction hit)와 **절대오차(MAE)**를 측정.

- **consensus**: 리듬이(HMM) · 눈치왕(GBM) · 파도꾼(TSFM) 3경로의 방향/오차 평균
- **baseline vs tuned**: 경로 파라미터(드리프트 스케일, empirical drift, TSFM blend 등) 그리드에서 모델별 best 선택 후 비교

---

## 2. 결과 요약 (한눈에)

### 3개월 방향 — 핵심 숫자

| 지표 | Baseline | Tuned | Δ |
|---|---:|---:|---:|
| Consensus direction hit | **53.2%** | 52.8% | −0.4%p |
| Consensus MAE (ret) | 0.0918 | **0.0853** | **−0.0064** |

해석:

- **방향 적중률**은 합의 기준으로 거의 동일(소폭 하락).
- **오차(MAE)는 유의미하게 줄었다** → 시나리오 진폭이 덜 과장되고, 흐름 차트상 “덜 날뛰는” 경로로 이어짐.
- 개별 모델 best (dense WF, n=163):

| 모델 | Hit | MAE | 채택 경로 파라미터 |
|---|---:|---:|---|
| HMM (리듬이) | **55.2%** | 0.0743 | empirical=False, scale=1.0 |
| GBM (눈치왕) | 47.2% | **0.0741** | empirical=False, scale=1.0 |
| TSFM (파도꾼) | **55.8%** | 0.1076 | empirical=True, scale=0.85 |

→ 방향만 보면 **HMM·TSFM이 GBM보다 낫고**, 오차만 보면 **HMM·GBM이 TSFM보다 낫다**.  
합의 설계(서로 다른 성격의 3경로)가 유효한 이유다.

### 국면 분류 — gold OOS

| 설정 | Accuracy | Macro-F1 |
|---|---:|---:|
| hmm_baseline / hmm_tuned | 0.220 | 0.166 |
| **gbm_baseline / gbm_tuned** | **0.222** | **0.176** |
| tsfm_baseline | 0.217 | 0.164 |
| tsfm_tuned (w=0.30/0.45/0.25) | 0.220 | 0.173 |
| tsfm_w_bal | 0.217 | 0.164 |

- winner: **gbm_tuned** (acc=0.222) — baseline GBM과 동일 점수지만, 캘리브레이션·트리 깊이 설정이 프로덕션 기본값으로 채택됨.
- 국면 accuracy ~22%는 6클래스·약한 신호 환경에서 **랜덤(≈16.7%)보다 약간 위** 수준. “맞히는 예측기”가 아니라 **국면 렌즈**라는 제품 포지션과 일치한다.

### Empirical daily drift (weak-label proxy)

국면별 prior 드리프트 vs 약라벨 기반 경험 드리프트:

| Regime | Prior | Empirical |
|---|---:|---:|
| 0 (A1) | +0.00035 | +0.00042 |
| 1 (A2) | +0.00055 | +0.00051 |
| 2 (A3) | +0.00015 | +0.00033 |
| 3 (B1) | −0.00025 | **+0.00034** |
| 4 (B2) | −0.00055 | **+0.00054** |
| 5 (B3) | −0.00035 | **+0.00087** |

하락 국면(B1–B3)의 empirical 값이 prior와 부호가 어긋나는 경우가 있어, **empirical drift를 전면 적용하면 방향 hit가 흔들릴 수 있다**.  
실험에서도 HMM/GBM best는 `empirical=False`였고, TSFM만 `empirical=True, scale=0.85`가 유리했다.

---

## 3. 프로덕션에 반영한 것

실험 후 코드에 승격된 설정 (배포 리비전 `kostolany-api-00014-*` 기준):

| 영역 | 변경 | 근거 |
|---|---|---|
| `KostolanyGBM` 기본 | `n_estimators=400`, `learning_rate=0.03`, `num_leaves=47` | 국면 winner / 경로 그리드에서 사용 |
| `TSFMEnsemble` 가중 | `w_hmm=0.30`, `w_gbm=0.45`, `w_tsfm=0.25` | tsfm_tuned 그리드 |
| Flows `_tsfm_path` | TSFM 일일 드리프트 스케일 **완화** (`*0.017`, clip ±0.003) + regime prior 비중 ↑ (`0.45/0.55`) | TSFM MAE 개선 (milder path) |

의도적으로 **반영하지 않은 것**:

- 하락 국면에 empirical drift를 전면 치환 → consensus hit에 손해 가능
- “방향 hit만 최대화”하는 단일 모델 승격 → 제품은 3인 합의 유지

---

## 4. 재현 방법

```powershell
# 가상환경에서
.\.venv\Scripts\python.exe scripts\run_forecast_experiment.py --symbol KS11 --splits 6

# 다른 시장
.\.venv\Scripts\python.exe scripts\run_forecast_experiment.py --symbol SPY
```

산출물:

- `artifacts/experiments/forecast_tune_<SYMBOL>_<UTC>.json`
- 동일 stem의 `.md` 스코어카드

Windows 콘솔(cp949)에서 유니코드 대시가 깨질 수 있어, 러너는 UTF-8 바이트로 요약을 출력한다. 상세는 `.md` 파일을 보면 된다.

---

## 5. 한계와 다음 실험 후보

1. **단일 심볼(KS11)** — SPY / BTC-USD에서도 같은 그리드가 이긴지 확인 필요.
2. **국면 accuracy 절대 수준이 낮다** — 분류 승격보다 **전환 감지·캘리브레이션·경제적 백테스트**(기존 `docs/HARNESS.md`)와 교차 검증이 우선.
3. **방향 hit ~53–56%** — 교육용 시나리오로는 설득력 있으나, 거래 시그널로 과장하면 안 됨 (제품 disclaimer와 동일).
4. **캐시/워밍업·429** — 이번 실험과 별개로 UX 고도화(캐시 우선·peek busy 구분)는 배포에 포함됨. 실험 메트릭과는 무관.

---

## 6. 한 줄 결론

> KS11 walk-forward에서 **합의 방향 적중은 ~53%로 유지**하면서 **합의 MAE를 약 7% 상대 개선**(0.0918→0.0853).  
> 개별로는 TSFM/HMM이 방향에, GBM/HMM이 오차에 강하고, 그 균형이 **완화된 TSFM 경로 + GBM 기본값 + TSFM 앙상블 가중**으로 프로덕션에 들어갔다.
