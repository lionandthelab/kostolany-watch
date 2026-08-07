# 데스크 판단 근거 레이어 — 설계 (2026-08-07)

오너 지시: *"사용자가 달걀 국면 예측과 더불어 추가적인 판단 근거를 더 볼 수 있게
하거나 과감한 분석 결과를 제시"*.

이 문서는 **모델을 개선하지 않는다.** 성능 상한은 실측으로 확정됐고
(`PERFORMANCE_LIMITS_2026-07-30.md`), exact6 0.29+는 산술적으로 불가능하다.
같은 정보에서 **더 많은 판단 근거**와 **더 명료한 표현**을 뽑는 것이 과제다.

동반 문서: [`DESK_JUDGMENT_PREREG_2026-08-07.md`](DESK_JUDGMENT_PREREG_2026-08-07.md)
(이 문서가 「측정 필요」로 분류한 것들의 사전등록).

> **읽는 법 — 「사용자가 보는 것」 블록은 전부 목업이다.**
> 그 안의 국면 라벨·날짜·거래일수·% 거리·원장 행은 레이아웃과 문장 구조를 보이기 위해
> 지어낸 **자리표시자**이며, 어느 것도 실측치나 실제 아카이브 기록이 아니다.
> 이 문서에서 실측으로 인용된 값은 출처(`PERFORMANCE_LIMITS_2026-07-30.md`,
> `calibration.py`, `momo.py`)를 명시한 것뿐이다. 구현 시 목업의 숫자를 옮겨 적지 마라 —
> 전부 서버가 계산해 내려주는 값으로 채워야 한다.

---

## 0. 이 설계가 지켜야 하는 것

| # | 규율 | 이 설계에서의 귀결 |
|---|---|---|
| 6 | 미측정 값을 측정된 것처럼 표시 금지 | **출고 후보의 모든 숫자는 (a) 이미 페이로드에 있는 값, (b) 결정론적 카운트, (c) 가격에서 나오는 닫힌 형태 산술 — 셋 중 하나여야 한다.** 이 세 부류 밖의 숫자는 사전등록 없이 화면에 못 올린다 |
| 7 | UI 카피에 확률/정확도 % 하드코딩 금지 | 신규 패널은 **적중률 %를 아예 렌더하지 않는다.** 카피 가드(`tests/test_conviction.py:91-100`)의 `\d+%` 패턴을 구조적으로 통과 |
| — | `confidence_spec.md §0.7` 숫자 예산 | 신규 정보는 **전부 접이식**. 헤드라인 카드에 추가되는 것은 % 없는 카운트 배지 한 줄뿐 |
| — | `confidence_spec.md §6` 금지 문장 17종 | §7에 각 항목 대조표 |

**"과감함"의 정의(이 문서의 조작적 정의):** 확신의 과장이 아니라
(1) **정보의 추가** — 지금 숨어 있는 결정론적 사실을 꺼내고,
(2) **자기 의심의 명시** — 사이트가 스스로 자기 판정의 약점을 지목하고,
(3) **경계의 노출** — 판정이 어디서 뒤집히는지를 산술로 보여주는 것.
세 가지 모두 새 측정을 필요로 하지 않는다.

---

## 1. 재고 조사 — 지금 페이로드에 실제로 무엇이 있는가

설계 전에 코드를 읽어 확인한 사실. **이 절이 「지금 출고 가능」 분류의 근거다.**

| 자원 | 위치 | 지금 화면에 쓰이는가 | 비고 |
|---|---|---|---|
| 4개 헤드 각각의 `snapshot.regime` | `api.py:157-190` `_build_watch_body` → `analysts[].snapshot` | **부분적** (`WatchApp.tsx:318-325`가 `rimFromProba` 로 재계산) | 4헤드 전부 매 페이로드에 있다 |
| momo 라이브 8규칙 투표 `vote` | `engine.py:159-190` `_vote_block` | 예 (배지·규칙 원장) | `rules[]` 에 규칙별 up/down |
| `analysts[].replay` (360프레임 × 4헤드) | `api.py:153,174` → `replay.py:91-97` | **아니오 — 통째로 버려진다** | `WatchApp.tsx:126-146`이 `snapshot`만 읽는다 |
| `calibration.confidence_view` (동결 실측표) | `calibration.py:109-152` | 예 | 심볼별. 무측정 심볼엔 키 자체 부재 |
| `calibration.measured` (4헤드 exact6/ECE/Brier) | `calibration.py:44-101` | 부분 (국면 모달의 focus 헤드만) | 4헤드 전부 있다 |
| 원장 아카이브 | `ledger.py:112-167`, `api.py:1064-1087` | 아니오 | `GET /api/ledger/{day}` 는 **이미 공개 엔드포인트** |
| 종가 시계열 | `engine._last_ohlcv` (두 momo 경로 모두 세팅: `engine.py:104,356`) | 간접 | 전환 거리 산술의 입력 |
| `next_likely[].proximity` | `engine.py:208-235` | 아니오 | **momo에서는 상수다 — §4 R2 참조** |

---

## 2. 후보 분류표 (이 문서의 핵심 산출물)

| # | 후보 | 분류 | 데이터 출처 | 비용 |
|---|---|---|---|---|
| **C1** | 헤드 불일치 패널 (가) | **지금 출고** | `analysts[].snapshot.regime` (이미 페이로드) | S |
| **C2** | 반대 증거 패널 (나, 게이지 제외) | **지금 출고** | `vote.rules` + C1 + C3 (전부 결정론적) | S |
| **C3** | 판정 전복 지점 = 규칙별 임계 거리 (바) | **지금 출고** | 종가 + `momo.MA_WINDOWS`/`RET_HORIZONS` 닫힌 형태 산술 | M |
| **C4** | 판정 지속 일수·최근 전환 (신규 제안) | **지금 출고** | `_last_pred.regimes` 런렝스 (카운트) | S |
| **C5** | 시장 간 대조 (신규 제안) | **지금 출고** | 다른 심볼의 캐시 peek (`peekWatch`, 기존 함수) | S |
| **C6** | 원장 「그날 우리가 띄운 것」 (마-1, 채점 없음) | **지금 출고 (2순위)** | `artifacts/ledger/day/*.json` 아카이브 문자열 | M |
| **C7** | 역사적 유사 국면 (다) | **측정 필요** | 새 실험 P-ANALOG-1 | L |
| **C8** | 티어 × horizon 조건부 실현 분포 (라) | **측정 필요** | 새 실험 P-COND-1 | M |
| **C9** | C1·C3·C4의 조건부 적중률 (「불일치하면 잘 틀리나?」) | **측정 필요** | 새 실험 P-DJ-1 / P-FLIP-1 / P-RUN-1 | L |
| **C10** | 원장 기반 트랙 레코드 채점 (마-2) | **측정 필요 (이미 사전등록됨)** | `LEDGER_SCORING_PREREG_2026-08-07.md` T2 이후 | — |

**분류 원칙:** C1–C6은 *사실의 제시*, C7–C10은 *사실에 의미를 붙이는 주장*이다.
전자는 지금 출고 가능하고, 후자는 사전등록된 측정이 선행되어야 한다.
**C1·C3·C4를 지금 출고하되 그 「의미」(조건부 적중률)는 C9가 통과할 때까지
화면에 붙이지 않는다** — 이것이 이 설계의 중심 구조다.

---

## 3. 지금 출고 가능한 후보 — 상세

### C1. 헤드 불일치 패널

**사용자가 보는 것** (접이식 「추가 판단 근거」 안):

```
AI 3종은 어디를 보고 있나
  추세 규칙 (기본)    B2  하락 레그
  리듬이 (HMM)        B1  하락 레그
  눈치왕 (GBM)        A3  상승 레그   ← 갈림
  파도꾼 (TSFM)       B2  하락 레그
  4개 중 3개가 하락 레그를 가리킵니다.

  기본 판정은 AI가 아닌, 사전 등록된 8개 추세 규칙의 다수결입니다.
  여기 표시는 각 헤드의 판정 자체일 뿐, 적중률과 연결되지 않습니다.
```

**데이터 출처:** 이미 페이로드에 있음. 4개 헤드의 `snapshot.regime`.
새 측정 0. 새 데이터 소스 0.

**규율 6 위반 위험:** 낮음. 렌더되는 값은 전부 서버가 이미 계산한 국면 코드다.
**진짜 위험은 "불일치의 의미"를 암시하는 것**이다 — 「헤드가 갈리면 적중률이
떨어집니다」는 미측정 주장이며 P-DJ-1이 통과해야 말할 수 있다.
회피: 마지막 고정 문장(「적중률과 연결되지 않습니다」)을 i18n 상수로 못박고,
이 패널 안에 **어떤 % 도 렌더하지 않는다**.

**규율 7 위반 위험:** 없음. % 자체가 없다.

**부수 효과 (버그 수정):** 현재 `WatchApp.tsx:318-325`의 `agreement`는
`rimFromProba`(확률 벡터의 원형 평균에 가장 가까운 칸)로 계산한다. 이는 각 헤드의
**자기 판정**(`snapshot.regime`, argmax)과 원리적으로 다른 값이며, 사후분포가
퍼진 헤드에서 갈릴 수 있다. C1을 서버 계산 필드로 옮기면 이 잠재 불일치가 사라진다.

**비용:** 백엔드 ~30 LOC + 테스트, 프론트 ~40 LOC + i18n. **S**

---

### C2. 반대 증거 패널 — "이 판정을 의심할 이유"

**사용자가 보는 것:**

```
이 판정을 의심할 이유
· 8개 규칙 중 1개가 반대편입니다: 10일 수익률 > 0
· 4개 헤드 중 1개가 다른 국면을 부릅니다: 눈치왕(GBM) — A3
· 오늘 종가가 지금보다 -4.6% 낮았다면 이 판정은 B2였습니다.
· 이 목록은 적중률과 연결되지 않습니다 — 지금 판정이 어디서 흔들리는지만 보여줍니다.
```

반대 신호가 하나도 없을 때:

```
· 지금은 반대 신호가 없습니다. 반대 신호가 없다는 것이 적중을 뜻하지는
  않습니다 — 만장일치 날에도 방향이 틀린 날이 있었고, 위 등급표의 적중률이
  그 사실을 그대로 담고 있습니다.
```

**데이터 출처:** 전부 파생 없음. 반대 규칙 = `vote.rules` 중 `vote !== vote.side`.
반대 헤드 = C1. 전복 거리 = C3. 새 측정 0.

**⚠ 오너 제안 (나)에서 의도적으로 뺀 것 — 게이지.**
지시문은 "게이지(volume/participation/money/sentiment)가 반대할 수 있다"를
반대 증거의 예로 들었으나, **이 저장소는 이미 그 주장을 명시적으로 철회했다**:

> `engine.py:383-391` `_build_context_gauges` docstring —
> *"NOT evidence for the regime call. The shipped head (`momo`) takes prices and
> nothing else... Calling them 'evidence' asserted a causal story the measurement
> does not support"*

게이지는 FRED 보강 피처 행렬에서 나오고, 서빙 헤드는 그것을 **입력으로 받지
않는다.** 판정의 입력이 아닌 값이 판정에 "반대"할 수는 없다. 게이지를 반대
증거로 올리는 것은 철회된 인과 서사를 뒷문으로 복원하는 것이므로 **기각**한다.
현재 화면의 `t.watch.contextNote`(비인과 공시)를 그대로 유지한다.

**규율 6 위반 위험:** 중. 「반대 근거가 N개면 위험」 같은 계량 암시가 새 나가기 쉽다.
회피: 목록은 **열거만** 하고 점수화·등급화하지 않는다. 카운트는 허용
(`confidence_spec §0.4` 「투표는 카운트」), 스코어는 금지.

**규율 7 위반 위험:** 없음(% 없음). 단, 「틀린 날 실측 N%」 = `1 − side_hit`를
쓰고 싶은 유혹이 있다 — **§8 D1 오너 결정 사항으로 분리했고, 이번 출고 범위에서
제외한다.**

**비용:** 프론트 전용 ~35 LOC + i18n. **S**

---

### C3. 판정 전복 지점 — 규칙별 임계 거리 (오너 후보 「바」)

> 지시문: *"실제로 가능한지 momo.py 를 읽고 판정하라."* → **가능하다. 닫힌
> 형태로 정확히 계산되며, 오차가 아니라 항등식이다.**

**유도.** `momo.py:78`의 규칙은 `px > px.rolling(w).mean()`이고, **롤링 평균은
오늘 종가 자신을 포함한다.** 마지막 봉의 종가를 반사실 값 `c`로 바꾸면
(다른 봉은 전부 고정), 창 크기 `m`에 대해

```
c > (S + c) / m   ⟺   c(m-1) > S   ⟺   c > S/(m-1) = 직전 m-1개 종가의 평균
```

즉 **ma_w 규칙의 경계값 = 직전 `m-1`개 종가의 산술평균**. 수익률 규칙
(`momo.py:80`, `px.pct_change(h) > 0`)의 경계값은 **`px[t-h]`** 그 자체다.
둘 다 이미 메모리에 있는 종가로 계산되고, 적합 파라미터 0개다.

**세 가지 구조적 성질을 실측 검증했다** (합성 가격 60계열 × 8규칙, 투표 상태
0~8 전부 커버, 실패 0건 — 검증 스크립트는 사전등록 문서 §9에 공시):

1. **부호 보장.** 지금 up을 던진 규칙의 경계값은 항상 오늘 종가보다 **아래**에
   있다. 즉 "얼마나 더 빠지면 뒤집히는가"가 항상 잘 정의된다.
2. **사다리 단조성.** 경계까지의 거리를 절대값 오름차순으로 정렬하면, k번째
   경계를 지날 때 up-표는 정확히 k개 줄어든다. 뒤집혔던 규칙이 되돌아오는 일은
   없다. → 등급 강등 지점과 방향 전복 지점이 유일하게 결정된다.
3. **시계 불변.** `labels_pit.pit_state`는 봉 t의 값을 읽지 않는다
   (`labels_pit.py:58-64`: 확인 창의 오른쪽 끝이 `t-1`). 따라서 이 반사실은
   **side만 바꾸고 third(초입/중간/말기)는 바꾸지 않는다.** 방향이 뒤집힌 판정은
   같은 칸 번호의 반대편 칸이다 (B2 ↔ A2).

**사용자가 보는 것:**

```
이 판정이 뒤집히는 지점
오늘 종가가 지금보다 …
· -0.2% 낮았다면  →  「종가 > 20일 이동평균」이 하락으로 갈립니다 (정렬 7-1)
· -1.4% 낮았다면  →  정렬 등급이 「우세」로 내려갑니다
· -4.6% 낮았다면  →  8규칙 다수결의 방향 판정 자체가 바뀌어 B2가 됩니다

이 값들은 규칙의 정의에서 나오는 산술입니다. 오늘 종가를 다른 값으로 바꿔
넣었을 때 같은 규칙이 어떻게 갈리는지일 뿐, 가격 예측도 매매 기준선도 아닙니다.
전환 시계(초입/중간/말기)는 오늘 종가를 읽지 않으므로 이 계산에서 바뀌지 않습니다.
```

**왜 「내일」이 아니라 「오늘 …였다면」인가 (설계 결정, 기각안 기록).**
다음 봉 기준(`c`가 새 봉으로 들어오는 경우)도 정확히 계산되지만, 그 경우 롤링
창이 가장 오래된 봉을 버리므로 **부호 보장이 깨진다** — 오늘 up인 규칙이 내일
더 *높은* 종가를 요구할 수 있다(실측 확인: 같은 검증 스크립트에서 반례 발생).
게다가 미래 시제는 가격 목표로 읽힌다(`confidence_spec §6 #3`). 따라서 **가정법
과거(same-bar counterfactual)를 채택**한다. 저장소의 「과거형 빈도」 화법과도 정합.

**규율 6 위반 위험:** 낮음(수치는 항등식). **진짜 위험은 규율이 아니라 오독이다** —
"−4.6%"가 지지선/목표가로 읽히는 것. 회피 3중:
1. **절대 가격 수준을 페이로드에 넣지 않는다.** `move_pct`만 실린다. 다운스트림
   에이전트가 가격 레벨을 렌더하는 것이 **구조적으로 불가능**하도록 만든다.
2. 문장을 항상 가정법 과거로 못박는다 (미래 시제 템플릿 금지).
3. 고정 부인 문구 2줄을 i18n 상수로 붙인다.

**규율 7 위반 위험:** 중. 이 패널은 % 를 렌더하는 유일한 신규 패널이다. 단,
이것은 **적중률이 아니라 가격 거리**이므로 `pctFloor`(실측 확률 전용 포맷터)를
쓰면 안 된다. §6.3에 전용 포맷터 `pctMove`와 그 반올림 방향 근거를 규정했다.

**절대 금지 (다운스트림 에이전트에게):** 전복 지점에 티어 적중률을 붙이는 것.
「−1.4% 내리면 적중률이 67%가 됩니다」는 **미래 조건부 확률 화법**이고
`confidence_spec §6 #2`에 정면으로 걸린다. 이 패널은 % 적중률을 **한 개도**
렌더하지 않는다.

**비용:** 백엔드 ~60 LOC(momo 헬퍼 + engine 배선 + fail-closed) + 테스트 ~60 LOC,
프론트 ~50 LOC. **M**

---

### C4. 판정 지속 일수·최근 전환 (신규 제안 — 오너 후보 「마」의 대체 경로)

**왜 신규 제안인가:** 「우리가 N일 전에 뭐라고 했나」라는 사용자 질문에는 두 개의
답이 있다. 원장 아카이브(C6)는 정확하지만 **지금 3~4행뿐**이다. 반면
`_last_pred.regimes`의 런렝스는 **같은 질문에 수년치로 답하고, 새 배선이 거의
없다.** 둘은 인식론적 지위가 다르므로 화면에서 반드시 구별해 표기한다.

**사용자가 보는 것:**

```
이 판정은 얼마나 오래됐나
· 하락 레그 판정이 37거래일째입니다 (2026-06-15부터).
· B2 칸 판정은 12거래일째입니다 (2026-07-21부터).

이 계산은 오늘의 규칙을 과거 가격에 다시 적용한 재계산이며, 그날 화면에 실제로
무엇이 떠 있었는지의 기록은 아닙니다.
방향(상승/하락) 판정은 8개 규칙만으로 결정됩니다. 칸(초입/중간/말기) 구분은
전환 시계의 삼분위에서 나오고, 그 삼분위는 전체 기간에서 잡혔습니다.
```

**데이터 출처:** `self._last_pred.regimes` 런렝스. 카운트일 뿐 새 측정 0.

**규율 6 위반 위험:** 중 — 두 개의 함정이 있고 둘 다 카피로만 막는다.
1. **재계산 ≠ 아카이브.** `LEDGER.md`가 문서화한 오염(`auto_adjust=True` 역조정,
   FRED 재서술)이 이 재계산에는 그대로 적용된다. 「우리가 그때 이렇게 말했다」로
   쓰면 허위. → 위 3번째 줄이 필수 고정 문구.
2. **side는 인과, third는 아니다.** 서빙 경로는 `MomoFloorHead().fit(close)` 후
   `predict(close)`이므로(`engine.py:350-351`) 시계 삼분위가 전체 기간에서 잡힌다.
   방향 런렝스는 순수 규칙 산물이라 깨끗하지만 칸 런렝스는 아니다. → 4번째 줄이
   필수 고정 문구이며, **칸 런렝스는 방향 런렝스보다 아래에 배치**한다.
3. 좌측 절단(표시 구간 시작까지 이어진 런)은 `truncated` 플래그로 「최소 N거래일」
   표기.

**규율 7 위반 위험:** 없음(% 없음).

**절대 금지:** 「37거래일째이므로 전환이 임박」류. 지속 일수의 의미는 P-RUN-1이
통과해야 말할 수 있고, 그 실험에는 위약 대조군이 걸려 있다(사전등록 §4).

**비용:** 백엔드 ~30 LOC, 프론트 ~25 LOC. **S**

---

### C5. 시장 간 대조 (신규 제안)

**사용자가 보는 것:**

```
다른 시장은
· 비트코인: A2 · 추세 신호 7-1 상승 정렬
두 시장은 각자의 데이터로 따로 판정됩니다. 한쪽이 다른 쪽의 근거가 되지 않습니다.
```

**데이터 출처:** 다른 심볼의 워치 캐시 `peek`. `api.ts`의 `peekWatch`가 이미 있고,
peek은 리빌드를 트리거하지 않는다(미스면 204). 백엔드 변경 0.

**규율 6 위반 위험:** 낮음. **단 `confidence_spec §6 #10`(타 심볼 수치 렌더 금지)에
직접 닿는다.** 회피: 이 패널은 **국면 코드·정렬 카운트·티어 이름만** 렌더하고
타 심볼의 **어떤 % 도 렌더하지 않는다.** 카운트는 §0.4에서 허용된다.
peek이 미스/버스트면 패널 자체를 렌더하지 않는다(빌린 값 폴백 절대 금지).

**비용:** 프론트 전용 ~45 LOC. **S**

---

### C6. 원장 「그날 우리가 띄운 것」 — 채점 없는 아카이브 뷰 (2순위)

**사용자가 보는 것:**

```
그날 우리가 띄운 것
YYYY-MM-DD   미국 <국면> (n-m)   비트코인 <국면> (n-m)
YYYY-MM-DD   미국 <국면> (n-m)   비트코인 <국면> (n-m)
YYYY-MM-DD   미국 <국면> (n-m)   비트코인 <국면> (n-m)

채점하지 않은 기록입니다 — 맞았는지 틀렸는지는 표시하지 않습니다.
채점 규칙은 결과를 보기 전에 따로 못박아 두었습니다.
기록 시작 <첫 기록일> · 현재 <N>일치.
```

**데이터 출처:** `artifacts/ledger/day/*.json` (GCS). 렌더되는 값은 전부 아카이브에
저장된 문자열이다. 새 측정 0. **새 읽기 엔드포인트는 필요**(§5.4).

**기존 사전등록과의 관계 — 반드시 읽을 것.**
`LEDGER_SCORING_PREREG_2026-08-07.md`는 T0(풀링 레그 8개 미만, 추정 2027 3Q까지)
동안 **채점 실행 자체를 금지**하고, KL8은 T2 미도달 상태의 **공개 스코어카드**를
금지한다. C6은 **채점이 아니다** — gold 라벨을 건드리지 않고, 적중/실패를 계산하지
않으며, 어떤 집계 지표도 만들지 않는다. 원장 원본은 이미 무인증 공개 엔드포인트
(`api.py:1064-1087`)로 서빙되고 있으므로 C6은 새로운 인식론적 주장을 만들지 않는다.
그럼에도 **KL8의 정신을 지키기 위해 세 가지를 하드 제약으로 못박는다**:
1. ✓/✗ 표시 금지, 색상으로 정오를 암시하는 것도 금지.
2. 어떤 집계(연속 일수 통계, 일치율, "N일 중 M일")도 금지. 행의 나열만.
3. 「채점하지 않은 기록」 고정 문구 + 사전등록 문서 참조를 항상 함께 렌더.

**왜 2순위인가:** 오늘 3~4행뿐이라 제품 가치가 얇고, C4가 같은 사용자 질문에
훨씬 긴 역사로 답한다. 다만 원장은 이 저장소의 **유일한 비대체 자산**이고, 화면에
올려두면 적재 중단이 즉시 눈에 띈다는 운영상 이점이 있다. 오너 판단 사항(§8 D3).

**규율 6 위반 위험:** 낮음(문자열 재생). 위험은 **사용자가 3행을 보고 스스로
채점하는 것**인데, 그것은 우리의 주장이 아니며 원장 공개의 취지이기도 하다.

**비용:** 백엔드 ~70 LOC(압축 엔드포인트 + 프로세스 내 캐시) + 테스트,
프론트 ~60 LOC(지연 로드) + `api.ts` 타입. **M**

---

## 4. 검토했으나 기각한 후보 (기각 사유를 남긴다)

| ID | 후보 | 기각 사유 |
|---|---|---|
| **R1** | 게이지 기반 반대 증거 (오너 후보 「나」의 일부) | `engine.py:383-391`이 게이지는 판정의 입력이 **아니라고** 명시적으로 공시한다. 입력이 아닌 값은 반대할 수 없다. 철회된 인과 서사의 뒷문 복원 |
| **R2** | `next_likely` 기반 「다음 국면 전이 가능성」 | **momo의 사후분포는 상수다.** `momo.py:99-117`: 호출 칸 `a·t3`, 같은 side의 다른 칸 `a(1−t3)/2`, 반대 side `(1−a)/3` — 전부 고정 상수. `proximity`를 「전이 가능성」으로 렌더하면 **매일 같은 숫자를 동적인 것처럼 파는 날조**가 된다. 영구 금지 |
| **R3** | `transition_score`(TSFM 내부값) 노출 | 미보정 모델 내부 스칼라. 측정된 대응물이 없다 |
| **R4** | 전복 지점에 티어 적중률 연결 (「−1.4%면 67%」) | 미래 조건부 확률 화법. `confidence_spec §6 #2` 정면 위반 |
| **R5** | AI 헤드의 `confidence`를 순위 이상으로 표시 | `calibration.py:19-21` G14 실패 → `confidence_is_calibrated` 영구 False. 「확신도」 어휘는 카피 가드가 저장소 전체에서 금지 |
| **R6** | 불일치·전복거리·지속일수의 **의미** 표기 | 그것이 C9이고, 사전등록이 선행되어야 한다. **C1·C3·C4를 출고하면서 여기까지 가고 싶은 유혹이 이 작업의 최대 실패 모드다** |

---

## 5. 백엔드 구현 지시 (필드명 확정 — 프론트와 맞물림)

> **전제:** 이 절의 필드명은 §6과 1:1로 못박혀 있다. 이름을 바꾸려면 두 문서를
> 같이 고쳐야 한다.

### 5.1 `momo.py` — 규칙 경계값 (신규 메서드 1개)

```python
def rule_flip_levels(self, prices: pd.Series) -> pd.Series:
    """Close value at the LAST bar that puts each rule exactly on its boundary.

    Same-bar counterfactual: substitute c for the final close, keep every other
    bar fixed. `close > MA_w` uses a window that CONTAINS c, so
    c > (S + c)/m  <=>  c > S/(m-1) — the mean of the m-1 prior closes.
    `ret_h > 0` is c > px[t-h]. Exact arithmetic, zero fitted parameters.

    Returns an EMPTY Series when the history is too short for every window to be
    full (min_periods would make the served vote NaN-driven); the caller then
    ships no flip block rather than a number computed on a different rule.
    """
```

* 반환: index = `RULE_IDS`(순서 동결), value = 경계 종가.
* 짧은 히스토리 가드: `len(px) <= max(MA_WINDOWS)` 또는 `<= max(RET_HORIZONS)` 이면
  **빈 Series** 반환.
* `prices`는 `dropna().astype(float).sort_index()` 후 사용.

### 5.2 `engine.py` — `EngineSnapshot`에 필드 2개 추가 (momo 전용, 그 외 `None`)

`_vote_block`과 **동일한 fail-closed 규율**을 상속한다. 예외는 삼키고 `None`.

```python
flip: dict[str, Any] | None = None   # 판정 전복 지점 (C3)
run:  dict[str, Any] | None = None   # 판정 지속 (C4)
```

**`flip` 스키마 (확정):**

```json
{
  "basis": "same_bar_close",
  "rules": [
    {"id": "ma20",  "vote": "up", "move_pct": -0.0023},
    {"id": "ma40",  "vote": "up", "move_pct": -0.0039}
  ],
  "steps": [
    {"split": "7-1", "tier": "strong", "move_pct": -0.0023},
    {"split": "6-2", "tier": "lean",   "move_pct": -0.0143},
    {"split": "5-3", "tier": "mixed",  "move_pct": -0.0305}
  ],
  "side_flip": {"from": "up", "to": "down", "regime_to": "B2", "move_pct": -0.0461}
}
```

* `rules`: **8개 전부**, `abs(move_pct)` 오름차순.
* `move_pct = level/close_at_ts - 1`. **음수 = 오늘 종가가 그만큼 낮았어야 함.**
  절대 가격은 **페이로드에 싣지 않는다**(§3 C3의 회피책 1).
* `steps`: k=1..8 사다리를 걸으며 `tier` 라벨이 **처음 바뀌는 지점만** 방출(≤3행).
  `tier`는 `_vote_block`과 동일한 매핑(`{8:unanimous,7:strong,6:lean, else mixed}`).
* `side_flip`: side가 뒤집히는 최소 이동. up-표 `u`에 대해 같은 side 규칙 중
  `u ≥ 4`면 `u-3`번째, `u ≤ 3`면 `4-u`번째 경계. **항상 존재한다**(증명: 같은 side
  규칙 수가 항상 필요 개수 이상). `regime_to` = 현재 regime id의 side 반전
  (`r < 3 ? r+3 : r-3`) — **third는 불변**(`labels_pit.py:58-64`).
* **fail-closed 조건(하나라도 걸리면 `flip = None`)**:
  1. `self._vote_rules is None` 또는 `_vote_block()`이 `None`
  2. `ts not in px.index` (px = `self._last_ohlcv["close"].dropna().sort_index()`)
  3. `rule_flip_levels(px.loc[:ts])`가 빈 Series
  4. **부호 불변식 위반**: 어떤 규칙에서 `(close_at_ts > level[rid])`가 서빙된
     `vote.rules[rid]`의 up/down과 불일치 → `None` + 로그
  5. `close_at_ts <= 0` 또는 `move_pct`에 비유한값

**`run` 스키마 (확정):**

```json
{
  "side": "down", "side_bars": 37, "side_since": "2026-06-15", "side_truncated": false,
  "regime": "B2", "regime_bars": 12, "regime_since": "2026-07-21", "regime_truncated": false,
  "grid_bars": 2790
}
```

* `regimes = self._last_pred.regimes.loc[:ts]` 위에서 후행 런렝스(오늘 포함).
* `side = regime_id < 3`. `*_truncated`는 런이 표시 구간 첫 봉까지 닿았을 때 `True`.
* momo 전용. AI 헤드는 `None`(그들의 regime 런은 fitted 모델의 재서술이라 지위가 다름).

### 5.3 `api.py` — 응답 모델 + 바디 필드

1. `SnapshotResponse`(`api.py:83-98`)에 추가:
   ```python
   flip: dict[str, Any] | None = None
   run: dict[str, Any] | None = None
   ```
2. `_build_watch_body`(`api.py:157-190`)에서 `body["head_dissent"]` 추가.
   **analyst가 2개 미만이면 키 자체를 넣지 않는다.**

```json
"head_dissent": {
  "n_heads": 4,
  "calls": [
    {"id": "momo", "regime": "B2", "side": "down"},
    {"id": "hmm",  "regime": "B1", "side": "down"},
    {"id": "gbm",  "regime": "A3", "side": "up"},
    {"id": "tsfm", "regime": "B2", "side": "down"}
  ],
  "side":   {"majority": "down", "n_agree": 3, "unanimous": false, "dissenters": ["gbm"]},
  "regime": {"majority": "B2",   "n_agree": 2, "unanimous": false}
}
```

* `side = regime.startswith("A") ? "up" : "down"` — **`snapshot.regime`에서만** 유도.
  확률 벡터에서 재계산하지 않는다(`rimFromProba` 경로와의 불일치 제거).
* 동수(2-2 등)일 때 `majority: null`, `unanimous: false`, `dissenters: []`.
  프론트는 「정확히 반반으로 갈립니다」를 렌더한다.
* `calls` 순서 = 요청된 `ids` 순서(momo가 첫 번째).

### 5.4 `api.py` — 원장 압축 엔드포인트 (C6 전용)

```
GET /api/ledger/recent?days=14        # days: 1..31, 기본 14
```

```json
{
  "days": [
    {"date": "2026-08-06",
     "calls": [{"symbol": "^GSPC", "asof": "2026-08-05", "regime": "B2",
                "split": "8-0", "tier": "unanimous", "side": "down"}]}
  ],
  "n_days": 3,
  "first_date": "2026-08-04",
  "scored": false,
  "prereg_doc": "docs/LEDGER_SCORING_PREREG_2026-08-07.md"
}
```

* **`model == "momo"` 행만**, `WATCH_MARKETS`만. 필드는 전부 아카이브에 이미
  있는 값(`ledger.py:146-164`)의 복사이며 **계산은 0**.
* `vote`가 `None`인 행(fail-closed 아카이브)은 `split`/`tier` 없이 `regime`만.
* 월 인덱스(`list_records`)로 존재하는 날짜를 먼저 알아낸 뒤 그 날짜만 읽는다.
  프로세스 내 TTL 캐시(30분) 필수 — GCS pull이 날짜당 1회 발생한다.
* `scored`는 항상 `false`. 미래에 사전등록 게이트를 통과해야 뒤집힌다.
  프론트는 `!scored`일 때 「채점하지 않은 기록」 문구를 **반드시** 렌더한다.

### 5.5 `watch_cache.py`

`WATCH_PAYLOAD_VERSION` `"v4"` → `"v5"` (`watch_cache.py:23`).
바디에 키가 추가되었으므로 기존 캐시를 무효화한다(선례: `confidence_spec §2.3`).
비용: 배포 후 최초 요청 시 2개 마켓 리빌드 — 기존 warmup 경로가 처리한다.

### 5.6 백엔드 테스트 게이트 (머지 조건)

| 파일 | 테스트 |
|---|---|
| `tests/test_momo.py` | `rule_flip_levels` 경계 양쪽 재투표: 8규칙 × ±eps 에서 `rule_votes`의 결과가 예측한 up/down과 일치 · 사다리 단조성(k번째 경계 통과 시 up-표 정확히 k 감소) · side_flip 인덱스 · **`pit_state`가 반사실 종가 하에서 불변** · 짧은 히스토리 → 빈 Series |
| `tests/test_conviction.py` | `flip`/`run`이 **두 momo 경로 모두**(단일 엔진 · `fit_analyst_bundle`)에서 채워짐 · AI 헤드는 전부 `None` · 부호 불변식 위반을 심으면 `flip is None`(fail-closed) · `flip` 페이로드에 **가격 단위 필드가 없음**(스키마 키 화이트리스트 검사) |
| `tests/test_conviction.py` | `head_dissent.calls[].side`가 `snapshot.regime`의 첫 글자와 항상 일치 · analyst 1개면 키 부재 · 동수에서 `majority is None` |
| `tests/test_conviction.py` | 카피 가드 기존 테스트가 신규 `judgment.*` 네임스페이스에도 초록 (자동 — 가드가 i18n 파일 전체를 스캔) |
| `tests/test_ledger.py` | `/ledger/recent`가 momo·WATCH_MARKETS 행만 반환 · `scored is False` · 집계 필드 부재 · 원장 없는 환경에서 `n_days == 0`으로 정상 응답 |

---

## 6. 프론트 구현 지시

### 6.1 배치

`WatchApp.tsx`의 `.regime-compact` 안, **기존 `<details> 자세히` 바로 아래에
형제 `<details>`를 하나 추가**한다. 기존 드로어의 내용·순서는 건드리지 않는다.

```
[1] B2 · 동행/하락 국면                       (기존)
[2] ●●●●●●●● 추세 신호 8개 중 8개 하락 정렬   (기존 AlignBadge)
[3] 방향 — … 실측 {p}                          (기존)
[4] 위치 — … 실측 {p}                          (기존)
[4.5] 헤드 4개 중 3개 하락 · 규칙 8개 중 1개 반대 · 하락 판정 37거래일째   ← 신규 (카운트만)
[5] ▸ 자세히                                   (기존, 무변경)
[5.5] ▸ 추가 판단 근거                          ← 신규 드로어
[6] 면책 문구                                   (기존, 자구 불변)
```

* **[4.5] 요약 줄은 % 를 하나도 포함하지 않는다.** `confidence_spec §0.7`의 숫자
  예산은 **% 예산**이며, 카운트는 §0.4에서 허용된다(기존 배지가 선례).
* [4.5]는 세 조각 중 존재하는 것만 `·`로 이어 붙인다. 전부 없으면 렌더하지 않음.

### 6.2 신규 드로어 「추가 판단 근거」 내부 순서

```
1) 이 판정을 의심할 이유        (C2)  — vote.rules + head_dissent + flip.side_flip
2) 이 판정이 뒤집히는 지점      (C3)  — flip
3) AI 3종은 어디를 보고 있나    (C1)  — head_dissent
4) 이 판정은 얼마나 오래됐나    (C4)  — run
5) 다른 시장은                  (C5)  — 다른 심볼 peek
6) 그날 우리가 띄운 것          (C6)  — /ledger/recent, 드로어가 열릴 때만 지연 로드
```

* **각 섹션은 자기 데이터가 `null`이면 통째로 렌더하지 않는다.** 6개 전부 없으면
  드로어 자체를 렌더하지 않는다.
* `focus !== "momo"`이면 1·2·4는 숨고 3·5·6은 남는다.
* 무측정 심볼(`confidence_view` 부재)에서도 이 드로어는 **정상 동작한다** —
  신규 패널에 적중률 % 가 없기 때문. `confidence_spec §5`의 무측정 경로가
  개선되는 부수 효과.

### 6.3 포맷터 — `eggGeometry.ts`에 하나 추가

```ts
/** Price DISTANCE, not a measured rate. Never route this through pctFloor:
 *  pctFloor is reserved for measured probabilities (spec §0.3).
 *  Magnitude truncates TOWARD ZERO so the display never overstates how much
 *  buffer a call has — understating the distance is the conservative error. */
export const pctMove = (x: number) =>
  `${x < 0 ? "-" : "+"}${Math.trunc(Math.abs(x) * 1000) / 10}%`;
```

`pctFloor`(카드용 정수, 실측 확률 전용)와 **혼용 금지**. 두 포맷터가 한 화면에
공존하므로, `pctMove` 값은 항상 부호와 「낮았다면/높았다면」 어구를 동반해 렌더한다.

### 6.4 i18n — 신규 네임스페이스 `judgment.*` (ko / en / types 3파일)

**모든 숫자는 슬롯 주입. 리터럴 `%`·숫자 금지** (`tests/test_conviction.py:99`의
`\d+%` 패턴은 i18n 파일 **전 줄**을 스캔한다. `.tsx`의 한글 주석도 스캔 대상이니
주석에 예시 % 를 쓰지 말 것).

```ts
judgment: {
  title: "추가 판단 근거",
  summary: {                                   // [4.5] 카운트 배지
    heads: "헤드 {n}개 중 {k}개 {side}",
    rules: "규칙 8개 중 {k}개 반대",
    run: "{side} 판정 {n}거래일째",
    runTruncated: "{side} 판정 최소 {n}거래일째",
  },
  doubt: {
    title: "이 판정을 의심할 이유",
    rules: "8개 규칙 중 {k}개가 반대편입니다: {list}",
    rulesNone: "8개 규칙 중 반대는 없습니다.",
    heads: "{n}개 헤드 중 {k}개가 다른 국면을 부릅니다: {list}",
    headsNone: "{n}개 헤드 전부 같은 레그를 가리킵니다.",
    flip: "오늘 종가가 지금보다 {d} {dir} 이 판정은 {regimeTo}였습니다.",
    none: "지금은 반대 신호가 없습니다. 반대 신호가 없다는 것이 적중을 뜻하지는 않습니다 — 만장일치 날에도 방향이 틀린 날이 있었고, 위 등급표의 적중률이 그 사실을 그대로 담고 있습니다.",
    note: "이 목록은 적중률과 연결되지 않습니다 — 지금 판정이 어디서 흔들리는지만 보여줍니다.",
  },
  flip: {
    title: "이 판정이 뒤집히는 지점",
    lead: "오늘 종가가 지금보다 …",
    rule: "{d} {dir} 「{ruleLabel}」이 {side}(으)로 갈립니다 (정렬 {split})",
    tier: "{d} {dir} 정렬 등급이 「{tierName}」(으)로 내려갑니다",
    side: "{d} {dir} 8규칙 다수결의 방향 판정 자체가 바뀌어 {regimeTo}가 됩니다",
    dirDown: "낮았다면 →",
    dirUp: "높았다면 →",
    note1: "이 값들은 규칙의 정의에서 나오는 산술입니다. 오늘 종가를 다른 값으로 바꿔 넣었을 때 같은 규칙이 어떻게 갈리는지일 뿐, 가격 예측도 매매 기준선도 아닙니다.",
    note2: "전환 시계(초입/중간/말기)는 오늘 종가를 읽지 않으므로 이 계산에서 바뀌지 않습니다.",
  },
  heads: {
    title: "AI 3종은 어디를 보고 있나",
    row: "{label} · {regime} · {side} 레그",
    agree: "{n}개 중 {k}개가 {side} 레그를 가리킵니다.",
    tied: "{n}개 헤드가 정확히 반반으로 갈립니다.",
    note: "기본 판정은 AI가 아닌, 사전 등록된 8개 추세 규칙의 다수결입니다. 여기 표시는 각 헤드의 판정 자체일 뿐, 적중률과 연결되지 않습니다.",
  },
  run: {
    title: "이 판정은 얼마나 오래됐나",
    side: "{side} 레그 판정이 {n}거래일째입니다 ({since}부터).",
    sideTruncated: "{side} 레그 판정이 최소 {n}거래일째입니다 (표시 구간 시작까지).",
    regime: "{regime} 칸 판정은 {n}거래일째입니다 ({since}부터).",
    regimeTruncated: "{regime} 칸 판정은 최소 {n}거래일째입니다 (표시 구간 시작까지).",
    note1: "이 계산은 오늘의 규칙을 과거 가격에 다시 적용한 재계산이며, 그날 화면에 실제로 무엇이 떠 있었는지의 기록은 아닙니다.",
    note2: "방향 판정은 8개 규칙만으로 결정됩니다. 칸 구분은 전환 시계의 삼분위에서 나오고, 그 삼분위는 전체 기간에서 잡혔습니다.",
  },
  cross: {
    title: "다른 시장은",
    row: "{market}: {regime} · 추세 신호 {split} {side} 정렬",
    note: "두 시장은 각자의 데이터로 따로 판정됩니다. 한쪽이 다른 쪽의 근거가 되지 않습니다.",
  },
  archive: {
    title: "그날 우리가 띄운 것",
    row: "{date} — {cells}",
    cell: "{market} {regime} ({split})",
    notScored: "채점하지 않은 기록입니다 — 맞았는지 틀렸는지는 표시하지 않습니다. 채점 규칙은 결과를 보기 전에 따로 못박아 두었습니다.",
    range: "기록 시작 {first} · 현재 {n}일치",
    empty: "아직 표시할 기록이 없습니다.",
  },
}
```

`en.ts`는 같은 의미를 영어로 미러. **금지 표현**: "probability of", "will",
"expected to", "target". 전복 지점은 반드시 가정법 과거 —
"had today's close been {d} lower, this call would have been {regimeTo}".

### 6.5 `api.ts` 타입 추가

```ts
export type FlipBlock = {
  basis: "same_bar_close";
  rules: { id: string; vote: "up" | "down"; move_pct: number }[];
  steps: { split: string; tier: string; move_pct: number }[];
  side_flip: { from: string; to: string; regime_to: string; move_pct: number } | null;
};
export type RunBlock = {
  side: "up" | "down"; side_bars: number; side_since: string; side_truncated: boolean;
  regime: string; regime_bars: number; regime_since: string; regime_truncated: boolean;
  grid_bars: number;
};
export type HeadDissent = {
  n_heads: number;
  calls: { id: string; regime: string; side: "up" | "down" }[];
  side: { majority: "up" | "down" | null; n_agree: number; unanimous: boolean; dissenters: string[] };
  regime: { majority: string | null; n_agree: number; unanimous: boolean };
};
export type LedgerRecent = {
  days: { date: string; calls: { symbol: string; asof: string; regime: string;
                                 split?: string; tier?: string; side?: string }[] }[];
  n_days: number; first_date: string | null; scored: boolean; prereg_doc: string;
};
```

`Snapshot`에 `flip?: FlipBlock | null; run?: RunBlock | null;`,
`WatchBundle`에 `head_dissent?: HeadDissent;` 추가.
`fetchLedgerRecent(days = 14)` 신규 — 드로어가 열릴 때만 호출, 실패 시 조용히 무시.

---

## 7. 금지 문장 17종 대조표 (`confidence_spec.md §6`)

| # | 금지 항목 | 신규 패널의 상태 |
|---:|---|---|
| 1 | 「확신도」·확신 밴드 | 미사용. 카피 가드가 저장소 전역에서 강제 |
| 2 | 미래 확률 화법 | 전복 지점은 **가정법 과거**. 미래 시제 템플릿 자체가 없음 |
| 3 | 시장 예보문·가격 목표·매매 타이밍 | 절대 가격을 **페이로드에 싣지 않음**(구조적 차단) + 부인 문구 2줄 |
| 4 | 라벨 없는 타 모집단 숫자 | 신규 패널에 적중률 % 자체가 없음 |
| 5 | 헤드라인 카드 안의 share % | [4.5]는 카운트만 |
| 6 | 「무작위 17%」 | 미사용 |
| 7 | 파생 비율 주조 | `1 − side_hit`(틀린 날 %)를 **출고 범위에서 제외**(§8 D1) |
| 8 | 「상한을 실측으로 확인」 | 미사용 |
| 9 | 올림 수치 | 신규 % 는 `pctMove`뿐이고 절대값을 0쪽으로 절사 |
| 10 | 타 심볼 수치 | C5는 코드·카운트만, % 0개 |
| 11 | 무측정 심볼의 적중률 | 신규 패널에 적중률 없음 → 무측정 심볼에서도 안전 |
| 12 | 「반원」·「반구」 | 미사용 |
| 13 | 자문 어휘 | 「의심할 이유」는 서술. 「권고/전략/매수·매도/관망」 미사용 |
| 14 | circularSpread 호에 붙은 커버리지 숫자 | 달걀 무변경 |
| 15 | 레그/방향 단어 없는 사이드 % | 사이드 % 미사용 |
| 16 | 「동전 던지기」 | 미사용 |
| 17 | 보정되었다는 암시 | 미사용 |

---

## 8. 오너 결정 사항 — 2026-08-07 전부 권고대로 확정

오너가 D1–D5를 권고안 그대로 승인했다. 아래 표의 「권고」가 곧 확정이며,
1차 출고는 그에 따라 이루어졌다:

- **D1 확정 — 표시하지 않음.** 파생 비율을 화면에서 주조하지 않는다. 숫자가
  필요해지면 아티팩트에 복합률 셀을 산출하는 경로만 허용한다.
- **D2 확정 — 예.** 절대 가격은 페이로드에 넣지 않는다.
- **D3 확정 — 1차 출고 제외.** `GET /ledger/recent`는 계약 유지를 위해
  구현되어 있으나 화면에는 렌더하지 않는다. 30일치 적재 후 재검토.
- **D4 확정 — 예.** `WATCH_PAYLOAD_VERSION`을 v5로 범프했다.
- **D5 확정 — P-DJ-1 착수, P-RUN-1 조건부, P-ANALOG-1 보류.**
  실행 완료: [`DESK_JUDGMENT_RESULTS_2026-08-07.md`](DESK_JUDGMENT_RESULTS_2026-08-07.md).
  1채택(P-DJ-1) 3기각. P-ANALOG-1은 보류 그대로.
- **D6 (신규, 2026-08-07) — C1에 적중률을 표기하지 않는다.** P-DJ-1이 채택됐으므로
  표기가 허가됐지만, 오너가 숫자 없는 사실 표시 유지를 선택했다. 근거와 재측정
  조건은 결과 문서 §2.3. **채택된 결과를 화면에 올리지 않는 것은 규율 위반이 아니다**
  — 채택은 표기를 허가할 뿐 요구하지 않으며, 측정값은 문서와 산출물로 공개돼 있다.

| # | 결정 | 권고 (= 확정) |
|---|---|---|
| **D1** | 「만장일치 날에도 틀린 날: 실측 N%」(= `1 − side_hit`)를 숫자로 표시할 것인가 | **권고: 이번엔 표시하지 않는다.** 두 표기 모두 결함이 있다 — `floor(1−0.8062)=19`는 화면에서 `80 + 19 = 99`가 되고, `100 − 80 = 20`은 실측 19.38%를 0.6%p 부풀린다(§6 #7 「파생 숫자 주조」에 닿음). C2는 숫자 없는 정성 문장으로 같은 일을 한다. 숫자가 필요하면 **아티팩트에 복합률 셀을 추가 산출**하는 것이 유일하게 깨끗한 길 |
| **D2** | C3(전복 지점)를 **절대 가격 없이 % 거리로만** 내는 안을 승인하는가 | **권고: 예.** 가격 레벨을 페이로드에 아예 넣지 않는 것이 지지선 오독에 대한 유일한 구조적 방어 |
| **D3** | C6(원장 아카이브 뷰)을 지금 낼 것인가, 원장이 더 쌓일 때까지 미룰 것인가 | **권고: 1차 출고에서는 제외, 30일치가 쌓인 뒤 재검토.** 3행짜리 표는 제품 가치가 얇고, 화면에 「채점하지 않음」 문구를 상시 노출하는 비용이 있다. C4가 같은 질문에 더 잘 답한다 |
| **D4** | `WATCH_PAYLOAD_VERSION` 범프에 따른 전 캐시 무효화(마켓 2개 리빌드)를 감수하는가 | **권고: 예.** 범프하지 않으면 TTL이 돌 때까지 패널이 보이지 않는 구간이 생긴다 |
| **D5** | C9(불일치·거리·지속의 조건부 적중률) 측정을 실제로 착수할 것인가 | **권고: P-DJ-1은 착수, P-RUN-1은 위약 대조군 포함 조건부 착수, P-ANALOG-1은 보류.** 사전등록 문서 §7의 우선순위 참조 |

---

## 9. 이 문서 작성 시점의 지식 상태 (정직성 공시)

* **새로 측정한 성능 수치는 없다.** 이 설계는 성능을 재지 않았다.
* C3의 산술 정확성만 합성 가격에서 검증했다(60계열 × 8규칙, 투표 상태 0~8 전부
  커버, 불일치 0건, 시계 불변 확인). **이것은 항등식 검증이지 성능 측정이 아니다.**
  검증 스크립트와 그 지위는 사전등록 문서 §9에 함께 공시한다.
* C7~C10의 어떤 셀도 계산된 바 없다. 사전등록 문서는 그 값을 보기 전에 쓰였다.

본 정보는 교육·연구 목적의 국면 인식 보조 자료이며 투자 권유·자문이 아닙니다.
