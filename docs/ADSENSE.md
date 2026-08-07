# AdSense 활성 절차

광고 유닛은 기본적으로 **꺼져 있다**. `web/src/AdSlot.tsx` 의 `ADSENSE_LIVE` 가
세 조건을 모두 통과해야 `<ins class="adsbygoogle">` 가 DOM 에 들어간다.

```ts
ADSENSE_LIVE = APPROVED && CLIENT.startsWith("ca-pub-") && /^\d+$/.test(SLOT)
```

하나라도 빠지면 `AdSlot` 은 `null` 을 반환한다 — 에러도, 빈 자리도 남기지 않는다.
그래서 "승인은 났는데 광고가 안 나온다" 와 "env 를 아예 안 넣었다" 가 화면상
구별되지 않는다. 개발 빌드(`npm run dev`)에서는 콘솔에 어떤 변수가 비었는지
한 줄로 찍힌다. 프로덕션에서는 아무 것도 찍지 않는다.

## 필요한 값 세 개

| 변수 | 위치 | 비고 |
| --- | --- | --- |
| `VITE_ADSENSE_CLIENT` | AdSense → 계정 → 게시자 ID | `ca-pub-` 로 시작. 이미 설정되어 있음 |
| `VITE_ADSENSE_SLOT` | AdSense → 광고 → 광고 단위 기준 → 단위 선택 → 코드 가져오기 → `data-ad-slot` | **숫자만.** 오너만 확인 가능 |
| `VITE_ADSENSE_APPROVED` | 사람이 판단 | 사이트 승인이 실제로 떨어진 뒤에만 `true` |

`index.html` 의 `<meta name="google-adsense-account">` 는 **계정 소유 확인용**이라
광고 로딩과 무관하다. 이게 들어 있다고 광고가 나오는 것이 아니다.

## 켜는 방법

1. `web/.env` 에서 아래 두 줄의 주석을 풀고 슬롯 ID 를 채운다.

   ```
   VITE_ADSENSE_SLOT=<AdSense 에서 복사한 숫자>
   VITE_ADSENSE_APPROVED=true
   ```

2. `cd web && npm run build`
3. 배포 (`.claude/skills/kostolany-deploy` 또는 `scripts/deploy-firebase.ps1`).

## 재빌드가 반드시 필요한 이유

`import.meta.env.*` 는 Vite 가 **번들 시점에 문자열로 치환**한다. 런타임 설정이
아니다. `.env` 만 고치고 배포하면 이미 빌드된 번들에는 예전 값(=없음)이 그대로
박혀 있어 아무 변화가 없다. 반대로 끌 때도 마찬가지로 재빌드가 필요하다.

## 광고를 넣는 위치

`AdSlot` 은 현재 `Landing.tsx` 의 finale 섹션 한 곳에서만 쓰인다. 슬롯 ID 를
유닛별로 나누려면 `<AdSlot slot="1234567890" />` 처럼 prop 으로 넘긴다 (숫자가
아니면 무시하고 `VITE_ADSENSE_SLOT` 으로 폴백한다).
