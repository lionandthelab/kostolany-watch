# Commercial-v4 모델 R&D 결과 (2026-07-28)

> 교육·연구용 국면 인식 및 확률 예측 실험이다. 투자 권유나 수익 보장이
> 아니며, 현재 후보는 아래 상용화 승격 기준을 모두 통과하지 못했다.

## 결론

기존의 고정 국면 드리프트/shifted-feature 기반 경로를 실제 미래 누적수익률을
직접 학습하는 다중 지평 모델로 교체했다. KS11 방향 적중과 국면 확률 품질은
개선됐지만, 시장 전체에서 단순 기준선을 안정적으로 이기지는 못했다.

- **연구 후보**: local direct-v4 예측기, conformal 불확실성 구간,
  direct regime ensemble
- **미채택**: affine 보정, 별도 이진 방향 헤드, 단기 전략 선택기, pooled-v1
- **현재 판정**: 연구 성과는 확인, commercial-v4 승격은 보류

## 변경된 모델

### 흐름 예측

`LocalTSFM`은 이제 `ret_20.shift(-h)` 같은 feature proxy가 아니라,
훈련 구간 안에서 완결되는 실제 미래 로그수익률을 직접 예측한다.

- 지평: 1/5/20/63 거래일
- 중앙값: robust LightGBM huber
- 구간: h63 q10/q90 + 시간순 conformal 반경
- 입력: 수익률, 다중 기간 변동성, 하방 변동성, 거래량-가격 상관,
  추세 가속도, 252일 범위 위치, 유동성/심리 등 causal feature
- magnitude: 시간순 보정 구간에서 부호를 바꾸지 않는 축소 계수만 추정
- production path: 고정 국면 표가 아니라 h5/h20/h63 직접 예측 anchor 사용

### 국면 예측

- GBM calibration을 일반 3-fold CV에서 시간순 holdout temperature calibration으로 변경
- 전일 posterior만 사용하는 causal cycle filter 추가
- 최종 비중: HMM 0.28 + temporal GBM 0.52 + direct trajectory 0.20
- 전이 불확실성 계산에서 전체 시계열 percentile rank를 제거하고 train-only scale 사용

### 계산 비용

TSFM ensemble 안에 이미 존재하는 HMM/GBM arm을 재사용한다. watch/flows 요청마다
세 엔진을 별도로 학습하던 경로를 한 번의 데이터 로드·feature build로 줄였다.
다만 direct head 자체는 기존 ridge proxy보다 훨씬 무겁다(로컬 측정 약 11초 대
0.2초). 따라서 **총 계산량이 줄었다고 볼 수 없으며**, 직렬 priority queue와
영속 cache가 production 전제다.

## OOS 결과

흐름 평가는 63 거래일 간격의 비중첩 expanding-origin이다. 각 시점의 h63 target은
훈련 종료 전에 완결된 행만 학습에 들어간다.

| 시장 | 표본 | 방향 적중 (95% block CI) | Always-up | MAE / trailing-drift | 80% 구간 coverage |
|---|---:|---:|---:|---:|---:|
| KS11 | 51 | **60.8% (45.1~72.5%)** | 51.0% | 1.015 | 74.5% |
| S&P 500 | 52 | 69.2% (53.8~80.8%) | **75.0%** | 1.050 | 84.6% |
| BTC-USD | 55 | 60.0% (45.5~74.5%) | 60.0% | **0.935** | 81.8% |

해석:

- KS11 방향은 기존 53~56%대 실험보다 개선됐고 always-up 대비 +9.8%p다.
- S&P 500은 절대 적중률이 높지만 장기 상승 기준선보다 낮다.
- BTC는 MAE를 개선했으나 방향 skill은 기준선과 같다.
- 모든 방향 CI가 넓고 KS11 CI도 always-up을 포함한다. 같은 origin에서 여러
  변형을 비교했으므로 60.8%는 독립 최종 holdout 통계가 아니다.
- 따라서 60%대 적중률만 보고 상용 성능이라고 주장할 수 없다.

Forecast convention은 종가 `t`까지 관측한 뒤 `t+1..t+63` 수익률을 맞히는 것이다.
이는 예측 정확도 평가이며 체결/PnL backtest가 아니다. 거래 성과로 해석하려면
별도 `execution_lag >= 1` backtest가 필요하다.

최종 국면 평가는 KS11 purged OOS 378개 관측치이며 gold label은 scoring에만 사용했다.

| 후보 | 정확도 | 인접 국면 정확도 | Macro-F1 | Brier | ECE |
|---|---:|---:|---:|---:|---:|
| GBM unsmoothed | 22.5% | 57.4% | 0.1816 | 0.204 | 0.522 |
| Direct ensemble | **24.6%** | **58.7%** | **0.1903** | **0.163** | **0.317** |

HMM posterior를 forward-only filter로 바꾸고 raw target 열을 HMM/GBM에서
제외한 최종 causal 평가다. 정확도·인접 정확도·macro-F1·Brier·ECE가 모두
개선됐다. 전이 탐지 hit는 fold별 bar 기준 0.839에서 0.806으로 낮아졌다.

## 폐기한 변형

1. **Unconstrained affine calibration**
   - KS11 방향 60.8% → 31.4%, correlation -0.222
   - 짧은 보정 구간이 부호를 뒤집는 selection overfit
2. **별도 이진 방향 classifier**
   - KS11 sparse OOS 61.5% → 50.0%, Brier 악화
3. **up/drift/momentum 선택기**
   - train holdout 승자가 실제 OOS에서 반복되지 않음
4. **pooled-v1 cross-asset model**
   - sparse screen 결과는 좋았으나 dense test에서 KS11 52.0%,
     S&P 500 50.0%, BTC 55.6%
   - origin 간격에 민감한 가짜 개선으로 판정, production 미승격

## 승격 기준과 현재 상태

- [x] 시장별 방향 적중 48% 이상
- [x] 80% 구간 coverage 65~95%
- [x] 국면 macro-F1 non-regression (+0.0087)
- [ ] always-up 대비 median 방향 skill +1%p 이상
- [ ] trailing-drift 대비 median MAE ratio 0.95 이하
- [x] gold/planted label 학습 미사용

## 재현

```powershell
# local direct-v4 + regime gate
.\.venv\Scripts\python.exe scripts\run_commercial_eval.py `
  --symbols KS11,^GSPC,BTC-USD --origin-stride 63 --refit-stride 252

# rejected pooled challenger
.\.venv\Scripts\python.exe scripts\run_pooled_forecast_eval.py `
  --targets KS11,^GSPC,BTC-USD --origin-stride 63 --refit-days 730

# repository leakage/test gate
.\.venv\Scripts\python.exe scripts\agent_verify.py
```

상세 반복 기록:
`.omc/autoresearch/commercial-v4/runs/20260728/decision-log.md`

## 다음 연구 우선순위

1. 개정값이 아닌 point-in-time macro vintage와 실제 시장 breadth 확보
2. 개발/선택/최종 holdout을 시장별로 분리한 champion registry
3. 전이 탐지용 duration-aware semi-Markov head
4. Chronos 계열 대형 모델은 동일 evaluator에서 challenger로만 비교
5. 최소 2~3년 paper/live forward 결과가 쌓이기 전 수익성·상용성 표현 금지
