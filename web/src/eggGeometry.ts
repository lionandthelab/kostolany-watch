/** Kostolany egg: points live on the outer perimeter, never the barycenter. */

export const CYCLE = ["A1", "A2", "A3", "B1", "B2", "B3"] as const;
export type RegimeCode = (typeof CYCLE)[number];

/** Angles (rad) around the egg rim — cycle A1→A3 up the right, B1→B3 down the left. */
export const REGIME_ANGLE: Record<RegimeCode, number> = {
  A1: (245 * Math.PI) / 180,
  A2: (325 * Math.PI) / 180,
  A3: (55 * Math.PI) / 180,
  B1: (110 * Math.PI) / 180,
  B2: (165 * Math.PI) / 180,
  B3: (205 * Math.PI) / 180,
};

export const EGG = {
  cx: 200,
  cy: 235,
  rx: 128,
  ry: 172,
  labelRx: 158,
  labelRy: 208,
};

export function pointOnRim(angle: number, rx = EGG.rx, ry = EGG.ry) {
  return {
    x: EGG.cx + rx * Math.cos(angle),
    y: EGG.cy - ry * Math.sin(angle),
  };
}

/** Circular mean of regime angles weighted by probability → rim point. */
export function rimFromProba(probs: Record<string, number>) {
  let sx = 0;
  let sy = 0;
  let mass = 0;
  for (const code of CYCLE) {
    const w = Math.max(0, probs[code] ?? 0);
    const a = REGIME_ANGLE[code];
    sx += w * Math.cos(a);
    sy += w * Math.sin(a);
    mass += w;
  }
  if (mass < 1e-9) {
    return { ...pointOnRim(REGIME_ANGLE.A2), angle: REGIME_ANGLE.A2, regime: "A2" as RegimeCode };
  }
  const angle = Math.atan2(sy, sx);
  // nearest labeled regime for color/copy
  let best: RegimeCode = "A2";
  let bestDist = Infinity;
  for (const code of CYCLE) {
    const d = angularDistance(angle, REGIME_ANGLE[code]);
    if (d < bestDist) {
      bestDist = d;
      best = code;
    }
  }
  return { ...pointOnRim(angle), angle, regime: best };
}

function angularDistance(a: number, b: number) {
  let d = Math.abs(a - b) % (Math.PI * 2);
  if (d > Math.PI) d = Math.PI * 2 - d;
  return d;
}

export const REGIME_GUIDE: Record<
  RegimeCode,
  { name: string; trait: string; volume: string; crowd: string; action: string; color: string }
> = {
  A1: {
    name: "수정(상승)",
    trait: "바닥 직후. 관심은 적지만 악재가 이미 많이 반영된 구간.",
    volume: "거래량 적음",
    crowd: "참여자 적음 → 서서히 증가",
    action: "분할 매수·축적",
    color: "#2F6FED",
  },
  A2: {
    name: "동행(상승)",
    trait: "추세가 우상향으로 자리 잡고 참여가 확산되는 구간.",
    volume: "거래량 증가",
    crowd: "참여자 증가",
    action: "보유·관망 (추격 매수 자제)",
    color: "#3D9B6E",
  },
  A3: {
    name: "과장(상승)",
    trait: "과열·열광. 모두가 낙관할 때 위험이 커지는 구간.",
    volume: "거래량 폭발",
    crowd: "참여자 최대",
    action: "분할 매도·차익 실현",
    color: "#D64545",
  },
  B1: {
    name: "수정(하락)",
    trait: "고점 직후 조정. 낙관이 남아 있지만 힘이 빠지기 시작.",
    volume: "거래량 감소·혼조",
    crowd: "참여자 감소 시작",
    action: "비중 축소·정리",
    color: "#C47A2C",
  },
  B2: {
    name: "동행(하락)",
    trait: "하락이 확산되고 심리가 얼어붙는 구간.",
    volume: "거래량 증가(투매)",
    crowd: "참여자 이탈",
    action: "관망·현금 비중 확대",
    color: "#8B6BB5",
  },
  B3: {
    name: "과장(하락)",
    trait: "공포·투매. 모두가 비관할 때 기회가 열리는 구간.",
    volume: "거래량 폭발",
    crowd: "참여자 최소",
    action: "분할 매수 (바닥 줍기)",
    color: "#1F4E8C",
  },
};

/** Public-facing analyst personas (ids stay API-stable). */
export const TOP_MODELS = [
  {
    id: "hmm",
    label: "리듬이",
    short: "리",
    color: "#2f5d50",
    trait: "메트로놈형",
    blurb: "말은 적고 박자는 정확. 숨은 국면 리듬만 집요하게 따라가는 은둔파 시계공.",
  },
  {
    id: "gbm",
    label: "눈치왕",
    short: "눈",
    color: "#c45c3e",
    trait: "잔가지 탐정",
    blurb: "거래량·심리·돈의 눈치를 재빠르게 훑어, 한 줄로 꽂아 주는 눈치 빠른 탐정.",
  },
  {
    id: "tsfm",
    label: "파도꾼",
    short: "파",
    color: "#4a7c9b",
    trait: "서퍼형",
    blurb: "긴 차트 물결을 통째로 읽고, 다음 굽이가 어디일지 가늠하는 파도타기 AI.",
  },
] as const;

export type ModelId = (typeof TOP_MODELS)[number]["id"];
