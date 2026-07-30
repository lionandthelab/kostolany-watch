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

/** Kostolany cycle is one-way: A1→A2→A3→B1→B2→B3→A1 */
export function forwardCyclePath(from: RegimeCode, to: RegimeCode): RegimeCode[] {
  const i0 = CYCLE.indexOf(from);
  const i1 = CYCLE.indexOf(to);
  if (i0 < 0 || i1 < 0) return [from, to];
  if (i0 === i1) return [from];
  const out: RegimeCode[] = [from];
  let i = i0;
  while (i !== i1) {
    i = (i + 1) % CYCLE.length;
    out.push(CYCLE[i]);
  }
  return out;
}

/** Angles increase CCW along our egg labeling — lerp that way between hops. */
export function lerpAngleAlongRim(a0: number, a1: number, t: number): number {
  let d = a1 - a0;
  while (d < 0) d += Math.PI * 2;
  while (d >= Math.PI * 2) d -= Math.PI * 2;
  return a0 + d * t;
}

/**
 * Sample rim angles from `from` → `to` along the cycle (not a chord through the egg).
 * Multi-hop = skipped intermediate regimes → denser samples so motion looks like a fast pass.
 */
export function anglesAlongCycle(
  from: RegimeCode,
  to: RegimeCode,
  stepsPerHop = 5,
): { angles: number[]; path: RegimeCode[]; skipped: RegimeCode[] } {
  const path = forwardCyclePath(from, to);
  const skipped = path.length > 2 ? path.slice(1, -1) : [];
  if (path.length <= 1) {
    return { angles: [REGIME_ANGLE[to]], path, skipped };
  }
  const denser = skipped.length > 0 ? Math.max(stepsPerHop, 3) : Math.max(2, Math.floor(stepsPerHop / 2));
  const angles: number[] = [];
  for (let h = 0; h < path.length - 1; h++) {
    const a0 = REGIME_ANGLE[path[h]];
    const a1 = REGIME_ANGLE[path[h + 1]];
    for (let s = 0; s < denser; s++) {
      angles.push(lerpAngleAlongRim(a0, a1, s / denser));
    }
  }
  angles.push(REGIME_ANGLE[to]);
  return { angles, path, skipped };
}

/** Human-readable skip interpretation for UI. */
export function cycleSkipNote(from: RegimeCode, to: RegimeCode): string | null {
  const path = forwardCyclePath(from, to);
  if (path.length <= 2) return null;
  const skipped = path.slice(1, -1);
  return `${from}→${to}: ${skipped.join("·")} 구간이 짧게 스킵된 것으로 해석합니다. 텔레포트가 아니라 사이클 동선을 빠르게 통과한 표현입니다.`;
}

/** Large rim jump or multi-hop cycle change → treat as path transit, not teleport. */
export function needsCycleTransit(from: RegimeCode, to: RegimeCode, fromAngle: number, toAngle: number): boolean {
  if (from === to) {
    return angularDistance(fromAngle, toAngle) > (35 * Math.PI) / 180;
  }
  const path = forwardCyclePath(from, to);
  if (path.length > 2) return true;
  return angularDistance(fromAngle, toAngle) > (45 * Math.PI) / 180;
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

/** Public-facing analyst personas (ids stay API-stable).
 * "momo" is the DEFAULT head: the zero-fitted-parameter trend vote that won
 * the pre-registered K2 gate. It is deliberately NOT framed as an AI. */
export const TOP_MODELS = [
  {
    id: "momo",
    label: "추세 규칙",
    short: "규",
    color: "#8a774a",
    trait: "무학습 기준",
    blurb:
      "8개 추세 규칙의 다수결 + 전환 시계로 국면을 부르는 기본 헤드. 학습된 AI가 아니며, 표시 확률은 실측 적중률과 일치하도록 고정.",
  },
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

/** Floor-only percent formatters — the only way numbers reach the screen.
 * Rounding UP a measured hit rate is a compliance violation (spec §0.3). */
export const pctFloor = (x: number) => `${Math.floor(x * 100)}%`;
export const pctFloor1 = (x: number) => `${Math.floor(x * 1000) / 10}%`;

const RING: readonly RegimeCode[] = ["A1", "A2", "A3", "B1", "B2", "B3"];

function circMid(a: number, b: number): number {
  const d = Math.atan2(Math.sin(b - a), Math.cos(b - a));
  return a + d / 2;
}

/** Rim polyline over a CONTIGUOUS run of sectors (ring order), spanning from
 * the boundary before the first to the boundary after the last. Boundaries are
 * circular midpoints of adjacent REGIME_ANGLE marks (the marks are NOT equally
 * spaced, so precomputed halves would be wrong). */
export function sectorBandPath(codes: RegimeCode[], rx = EGG.rx, ry = EGG.ry): string {
  if (!codes.length) return "";
  const first = codes[0];
  const last = codes[codes.length - 1];
  const prev = RING[(RING.indexOf(first) + RING.length - 1) % RING.length];
  const next = RING[(RING.indexOf(last) + 1) % RING.length];
  const way = [
    circMid(REGIME_ANGLE[prev], REGIME_ANGLE[first]),
    ...codes.map((c) => REGIME_ANGLE[c]),
    circMid(REGIME_ANGLE[last], REGIME_ANGLE[next]),
  ];
  let acc = way[0];
  const p0 = pointOnRim(acc, rx, ry);
  const pts = [`M${p0.x.toFixed(2)},${p0.y.toFixed(2)}`];
  for (let i = 1; i < way.length; i++) {
    const d = Math.atan2(Math.sin(way[i] - acc), Math.cos(way[i] - acc));
    const steps = Math.max(2, Math.ceil(Math.abs(d) / 0.1));
    for (let sIdx = 1; sIdx <= steps; sIdx++) {
      const a = acc + (d * sIdx) / steps;
      const q = pointOnRim(a, rx, ry);
      pts.push(`L${q.x.toFixed(2)},${q.y.toFixed(2)}`);
    }
    acc += d;
  }
  return pts.join(" ");
}

export const SIDE_SECTORS: Record<"up" | "down", RegimeCode[]> = {
  up: ["A1", "A2", "A3"],
  down: ["B1", "B2", "B3"],
};

export function zoneSectors(center: RegimeCode): RegimeCode[] {
  const i = RING.indexOf(center);
  return [RING[(i + RING.length - 1) % RING.length], center, RING[(i + 1) % RING.length]];
}

/** Circular std (radians) of a 6-class posterior over the ring — the angular
 * half-width of the egg arc. sigma = sqrt(-2 ln R), clamped to a drawable band. */
export function circularSpread(probs: Record<string, number>): number {
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
  if (mass < 1e-9) return Math.PI * 0.9;
  const R = Math.min(1 - 1e-9, Math.hypot(sx, sy) / mass);
  const sigma = Math.sqrt(-2 * Math.log(R));
  return Math.min(Math.PI * 0.9, Math.max(0.12, sigma));
}
