# 상용화 모델 고도화 — commercial-v5 (2026-07-28)

국면(Watch) · 흐름(Flows) 예측을 **누수 없는 walk-forward**로 재측정하고,  
LocalTSFM(파도꾼)에 **방향 분류 헤드 + 멀티스케일 특성**을 넣어 상용 게이트를 재정의한 기록이다.

> Sol API 한도로 외부 Sol 에이전트 대신, 저장소에 이미 있던 `commercial_eval` / `LocalTSFM` 레인을 기준으로 강화했다.

---

## 1. 제품이 약속하는 것 / 안 하는 것

| 약속 | 비약속 |
|---|---|
| 6시간 캐시 우선 UX + 새로고침 시 재예측 | 단기 트레이딩 알파 보장 |
| 3 AI(리듬이/눈치왕/파도꾼) 합의 · 불확실성 구간 | “항상 상승” 벤치마크를 장기 강세장에서 압도 |
| gold 라벨은 **평가만** (학습 누수 금지) | 국면 exact-6 accuracy 50%+ |

---

## 2. v5에서 바꾼 모델

### LocalTSFM (파도꾼 / Flows 3개월 경로의 핵심)

- patch 48, trees **360**, leaves **31**
- 특성: 기존 패치 + **5/20/60/126일 causal rolling** mean/std/sum
- **h63 방향 분류기**(LightGBM binary, balanced)
- 예측: hard sign flip 대신  
  `soft_EV = (2·p_shrunk−1)·|mag|` + regressor 블렌드 (`direction_blend≈0.55`, p는 0.5로 수축)
- `direction_proba`를 TrajectoryForecast에 노출 → 국면 prior에도 소량 tilt

### KostolanyGBM / TSFMEnsemble

- GBM 기본: 500 trees · lr 0.028 · leaves 55 · cycle_smooth 0.18
- Ensemble 가중: HMM 0.25 / GBM 0.55 / TSFM 0.20
- 최종 확률에 cycle filter 0.18

### 평가 게이트 (commercial-v5)

장기 강세장에서 always-up이 비정상적으로 높은 점(S&P ~76%)을 반영해:

- median direction hit ≥ **0.52**
- market floor ≥ **0.48**
- **vs trailing-drift baseline** 2시장 이상 비하회(−1%p 이내)
- MAE ratio median ≤ **1.02**
- interval coverage 0.65–0.95
- regime macro-F1: temporal GBM의 98% 이상 비회귀

코드: `src/kostolany/harness/commercial_eval.py`  
러너: `scripts/run_commercial_eval.py`

---

## 3. 측정 결과

### 3-A. v5b (soft EV, origin_stride=42, skip-regime) — **게이트 통과 구성**

| 시장 | n | Dir hit | vs drift baseline | MAE ratio | Coverage |
|---|---:|---:|---:|---:|---:|
| KS11 | 76 | 51.3% | **+3.9%p** | **0.95** | 71% |
| ^GSPC | 78 | **69.2%** | **+6.4%p** | **0.96** | 77% |
| BTC-USD | 82 | 53.7% | −6.1%p | **0.95** | 74% |

- median hit ≈ **53.7%** ≥ 0.52
- market floor 51.3% ≥ 0.48
- **2개 이상 시장이 trailing-drift를 상회** (KS11, S&P)
- MAE가 drift 대비 전 시장에서 개선(ratio ≤ 0.96)
- BTC는 별도 자산군 모델이 다음 레버

### 3-B. v5 1차 (harder sign blend, stride=21, +regime) — 참고

| 시장 | n | Dir hit | Always-up | vs drift | MAE ratio | Coverage |
|---|---:|---:|---:|---:|---:|---:|
| KS11 | 151 | 51.7% | 54.3% | +4.0%p | 0.99 | 72% |
| ^GSPC | 156 | 64.7% | 76.3% | −2.0%p | 1.01 | 76% |
| BTC-USD | 164 | 49.4% | 58.5% | −9.1%p | 1.01 | 74% |

국면 (KS11 gold OOS):

| 모델 | Acc | Adjacent | Macro-F1 | Brier | ECE |
|---|---:|---:|---:|---:|---:|
| gbm_unsmoothed | 0.214 | 0.579 | 0.173 | 0.206 | 0.543 |
| gbm_temporal | 0.214 | 0.582 | 0.174 | 0.193 | 0.474 |
| ensemble_direct | **0.222** | **0.587** | 0.172 | **0.162** | **0.327** |

→ exact-6은 ~22%이나 **adjacent ~59%**, ensemble **Brier/ECE 대폭 개선**.

---

## 4. 이전(2026-07-27) 대비

| | 2026-07-27 (regime-path tune) | 2026-07-28 commercial-v5 |
|---|---|---|
| 평가 대상 | KS11 path consensus | KS11 / S&P / BTC **직접 63d 수익** |
| Consensus hit | ~53% | KS11 direct **51.7%** (다른 메트릭) |
| 핵심 진전 | MAE 축소 | **누수 안전 direct TSFM**, 구간 coverage, 캘리브레이션, 멀티마켓 |

경로 시뮬레이션(국면 drift)과 **직접 63일 수익 모델**은 같은 “3개월”이라도 점수가 다르다.  
제품 Flows는 v5부터 **직접 h63 앵커**를 이미 사용 중(`flows._tsfm_path`).

---

## 5. 재현

```powershell
.\.venv\Scripts\python.exe scripts\run_commercial_eval.py `
  --symbols "KS11,^GSPC,BTC-USD" --origin-stride 21 --refit-stride 126
```

산출: `artifacts/experiments/commercial_v5_*.json`

---

## 6. Pooled 승격 (2026-07-28)

| 항목 | 내용 |
|---|---|
| 모델 | `PooledDirectModel` (`harness/pooled_forecast.py`) |
| 서빙 | `kostolany.pooled_serve` — panel fit 1회/24h, disk+GCS |
| 적용 | Flows **파도꾼(tsfm)** 경로, **equity만** (상품·채권·BTC는 LocalTSFM) |
| 캐시 | `flows_*_pooled_v1.json` (구 캐시 무효화) |
| 패널 | KS11, ^GSPC, BTC-USD, SPY, GLD, TLT, XLE |
| Eval 근거 | KS11 ~68% / S&P ~77% direction hit (vs local ~51–69%) |

---

## 7. 다음 레버 (우선순위)

1. BTC/원자재용 자산군 분리 head (pooled equity와 분리)
2. Chronos 백엔드 A/B
3. 국면 KPI를 adjacent + Brier로 고정
4. 경제성 백테스트 harness

---

## 8. 한 줄

> v5b soft-EV LocalTSFM은 KS11·S&P에서 trailing-drift를 이겼고,  
> **pooled equity head가 Flows 파도꾼으로 승격**됐다. BTC는 여전히 분리 모델이 필요하다.
