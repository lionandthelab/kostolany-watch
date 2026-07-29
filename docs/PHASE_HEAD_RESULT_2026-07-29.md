# S0/S2 실행 결과 — 위상 헤드는 승격하지 않는다 (2026-07-29)

계획: [TAB_MODEL_ARCHITECTURE_2026-07-29.md](TAB_MODEL_ARCHITECTURE_2026-07-29.md) · 감사: [AI_RND_PLAN_2026-07-29.md](AI_RND_PLAN_2026-07-29.md)
산출물: `artifacts/experiments/phase_head_KS11_20260729T084539Z.{json,md}` (gitignore 대상 — 재현 명령은 아래)

---

## 1. 결정

> **`PhaseHead`는 사전 등록한 S2 게이트 5개를 모두 통과하지 못했다. 서빙 경로에 넣지 않는다.**

게이트를 통과하지 못한 모델을 "그래도 기존보다 나으니" 승격하는 것은 이 저장소가
`commercial-v5`에서 이미 한 번 했던 실패 — 실패한 게이트를 코드에서 삭제해 통과로 만든 그 패턴 —
과 같다. 반복하지 않는다. 코드(`src/kostolany/phase.py`, `scripts/run_phase_experiment.py`,
`tests/test_phase.py`)는 연구 레인으로 저장소에 남기고, 어떤 API 경로도 이를 호출하지 않는다.

---

## 2. 측정 결과

KS11 · purged walk-forward(`anchor="end"`) · 8 폴드 · **OOS 2,696거래일 (2015-07 ~ 2026-07)** ·
독립 레그 35개 · gold 라벨은 채점 전용 · 학습 타깃은 causal weak-label 각도.

| 후보 | exact-6 | adjacent | macro-F1 | Brier | ECE |
|---|---:|---:|---:|---:|---:|
| gbm_shipped | 0.2103 | 0.5490 | **0.1924** | 0.1820 | 0.4244 |
| ensemble_shipped | 0.1977 | 0.5723 | 0.1568 | 0.1961 | 0.5028 |
| **phase_head** | **0.2140** | **0.5861** | 0.1848 | 0.1501 | 0.1904 |
| weak_label (0-파라미터) | 0.1925 | 0.5716 | 0.1771 | 0.2692 | 0.8075 |
| **prior_shrunk (상수)** | 0.1836 | 0.5367 | 0.0800 | **0.1414** | **0.0442** |
| best_constant_oracle | 0.1806 | 0.5423 | 0.0510 | 0.2731 | 0.8194 |

**사전 등록 목표 대비**: exact-6 0.29 / adjacent 0.74 / 순환거리 1.02 / ECE 0.07 / Brier 0.134
→ **5개 전부 미달.**

**Kill 조건**: "순환거리 CI가 최적 상수를 포함하면 중단". primary 레이아웃의 CI **[1.2126, 1.4783]는
최적 상수 1.4304를 포함**한다. 감도 레이아웃(min_train=756, 42 레그)에서는 포함하지 않는다.
두 레이아웃이 갈리는 것 자체가 결론이며, 사후에 통과하는 쪽을 고르는 것은 금지한다.

**짝지은 leg-block 부트스트랩** (phase_head − 상대, `*` = 95% CI가 0 배제):

- vs `gbm_shipped`: Brier **−0.0319\***, ECE **−0.2339\*** — 캘리브레이션은 확실히 개선
- vs `ensemble_shipped`: Brier **−0.0460\***, ECE **−0.3124\***
- 순서 지표(adjacent, 순환거리, exact-6)는 primary에서 **전부 유의하지 않음**
- vs `prior_shrunk`: Brier **+0.0087\* 악화**, ECE **+0.1462\* 악화** — 상수 예측기를 못 이김

**정합성 트립와이어**: side 정확도 0.5753 × 조건부 third 정확도 0.3720 = exact-6 0.2140,
`product_gap = 0.0000` → 배관 검증됨. 6분류 타깃의 학습 가능한 자유도가 하나뿐이라는 진단이
실측으로 재확인됐다.

**경고**: 독립 레그가 35개뿐이다(S0 게이트 ≥50 미달). 현재 gold 정의와 min_train=1260에서는
최대 OOS가 2,701봉이라 ≥50 레그가 구조적으로 도달 불가능하다. 후보 간 순위는 약하게만 식별된다.

---

## 3. 그럼에도 배포하는 것 — 검증된 결함 수정

승격이 아니라 **버그 수정**이므로 통계 게이트가 필요 없는 항목들이다.

| # | 수정 | 파일 | 검증된 효과 |
|---|---|---|---|
| 1 | 평가창이 데이터의 9.5%만 덮던 walk-forward | `harness/cv.py` (`anchor` 파라미터) | OOS 378봉/2011-2012 → **2,696봉/2015-2026** |
| 2 | HMM 상태→국면 매핑이 다수결로 전단사를 파괴 | `models.py` (`linear_sum_assignment`) | **구조적 확률 0 국면 4개 → 0개** |
| 3 | weak 라벨 B1 항의 `/0.12` 정규화 누락 | `labels.py` | B1 점유 19.9%→11.2%, A1 10.5%→19.9% |
| 4 | `gold_labels` 최소 간격이 캘린더일/거래봉 혼용 | `labels.py` | 전환점 81→54, gold 클래스 분포 균형화 |
| 5 | TSFM 설계행렬이 최근 3봉을 표본에서 누락 | `tsfm.py` (`_patch_matrix`) | 최신봉 섭동 응답 **0.0 → 10.0** |
| 6 | h63 방향 분류기가 부호를 바꿀 수 없어 기여도 0 | `tsfm.py` | 분류기가 실제로 6.99% 봉의 부호를 결정 |
| 7 | `forecast_engine`이 폴백을 `local_tsfm`으로 오표기 | `flows.py` | `regime_prior_fallback`로 정정 |
| 8 | q10/q90/p_up이 계산 후 폐기 | `flows.py`, `web/src/*` | 밴드 콘 + 상승확률 칩이 화면 도달 |
| 9 | 게이트가 스킵되어도 통과로 집계 / `×0.98` 여유 | `harness/commercial_eval.py` | always-up 하드 게이트 복원, 스킵은 하드 실패 |
| 10 | 신뢰도를 문자 그대로 % 렌더 (실측 ECE 0.42~0.50) | `calibration.py`, `WatchApp.tsx` | 밴드 + **실측 성적 병기** |
| 11 | ChronosTSFM 사장 코드 / build-essential | `tsfm.py`, `Dockerfile` | 컴파일러 툴체인 ~290MB 제거 |

### 3-1. 신뢰도 표시가 바뀐 이유

배포 직전 프로덕션 API의 실제 응답:

```json
"confidence": 0.9999999999973577,
"probabilities": {"A1": 0.0, "A2": 5.2e-42, "A3": 1.9e-18, "B1": 2.6e-12, "B2": 0.99999..., "B3": 0.0}
```

**화면에는 100%로 표시**되고 있었다. 같은 스택의 실측 ECE는 0.42~0.50, exact-6 정확도는 0.21이다.
`A1`과 `B3`은 정확히 0 — 발생 가능성이 아예 없는 국면으로 선언되고 있었다.

수정 후(동일 입력): 세 애널리스트 모두 `zero_classes=0`, ensemble 신뢰도 0.539.
UI는 원시 퍼센트 대신 **확신도 밴드(높음/보통/낮음)** 와 **측정 성적 각주**를 표시한다 —
"6국면 정확 적중 21%(무작위 17%) · 인접 포함 57%(구조적 하한 50%)".

---

## 4. 재현

```powershell
# 위상 헤드 실험 (네트워크 불필요 — 캐시된 KS11 파켓 사용)
.\.venv\Scripts\python.exe scripts\run_phase_experiment.py --symbol KS11
# 감도 레이아웃
.\.venv\Scripts\python.exe scripts\run_phase_experiment.py --symbol KS11 --min-train 756 --n-splits 10
# 검증 게이트
.\.venv\Scripts\python.exe scripts\agent_verify.py
```

---

## 5. 다음

1. **S0 커버리지 게이트(≥50 레그)가 현재 gold 정의에서 도달 불가**하다. min_train을 낮추든
   gold 정의를 바꾸든 결정이 필요하며, **통과하는 쪽을 사후에 고르지 않도록 먼저 정해야 한다.**
2. 기준선 상수(1.3709/1.4852/0.1382/0.2508)는 전부 **옛 gold 정의**에서 나온 값이다.
   수정된 gold 기준 실측은 최적상수 순환거리 **1.4304**, 상수-사전 Brier **0.1387**, 다수결 **0.1817**.
   S2 판정을 인용하기 전에 재-사전등록이 필요하다.
3. Lane C의 q10–q90 콘이 S4 게이트(PIT χ² p>0.05, 폭 비율 <0.85) 없이 렌더된다.
   콘은 pooled 헤드의 분위 헤드에서 오는데, 그 헤드는 게이트도 저장 산출물도 없이 승격된 것이다.
4. `flows.outlook`은 여전히 `end >= 100`, 즉 방향 스킬 0으로 측정된 점추정의 부호다.
