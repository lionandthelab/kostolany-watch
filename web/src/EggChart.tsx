import {
  CYCLE,
  EGG,
  REGIME_ANGLE,
  REGIME_GUIDE,
  SIDE_SECTORS,
  circularSpread,
  pointOnRim,
  rimFromProba,
  sectorBandPath,
  zoneSectors,
  type ModelId,
  type RegimeCode,
} from "./eggGeometry";
import { useT } from "./i18n";

export type ModelMark = {
  id: ModelId;
  /** Full display name (hover tooltip). */
  label: string;
  color: string;
  probabilities: Record<string, number>;
  confidence: number;
  /** When set, pin sits on this rim angle (cycle transit animation). */
  angleOverride?: number;
};

type Props = {
  models: ModelMark[];
  /** Highlight which model is “focus” for briefing — others still drawn */
  focusId?: ModelId;
  onFocus?: (id: ModelId) => void;
  /** Full-block spinner only when nothing is ready yet */
  loading?: boolean;
  /** Soft status while remaining models compute */
  pendingLabel?: string | null;
  /** Conviction layers (spec: research/confidence_spec.md §3.3). The side
   * wash opacity is a DISCRETE tier ladder — an ordinal display constant,
   * never a measured value (the legend says so). */
  sideBand?: { side: "up" | "down"; tier: "unanimous" | "strong" | "lean" | "mixed" } | null;
  /** Call ±1 sector zone band (fixed faint opacity). */
  zoneCenter?: RegimeCode | null;
};

const SIDE_TIER_OPACITY: Record<string, number> = {
  unanimous: 0.26,
  strong: 0.18,
  lean: 0.1,
  mixed: 0,
};

function labelAnchor(angle: number): { dx: number; dy: number; anchor: "start" | "middle" | "end" } {
  const c = Math.cos(angle);
  const s = Math.sin(angle);
  // Extra outward offset so rim codes clear the shell (esp. left side).
  if (c > 0.35) return { dx: 16, dy: 4, anchor: "start" };
  if (c < -0.35) return { dx: -18, dy: 4, anchor: "end" };
  if (s > 0.2) return { dx: 0, dy: -14, anchor: "middle" };
  return { dx: 0, dy: 20, anchor: "middle" };
}

export function EggChart({
  models,
  focusId,
  onFocus,
  loading = false,
  pendingLabel = null,
  sideBand = null,
  zoneCenter = null,
}: Props) {
  const t = useT();
  const marks = models.map((m, i) => {
    const rim = rimFromProba(m.probabilities);
    const fan = (i - (models.length - 1) / 2) * 0.045;
    const ang = (m.angleOverride ?? rim.angle) + fan;
    const p = pointOnRim(ang);
    return { ...m, ...rim, angle: ang, x: p.x, y: p.y, spread: circularSpread(m.probabilities) };
  });
  // S4: the focused head renders as an ARC on the rim — angular extent = the
  // posterior's circular spread — so an uninformative bar reads as a wide arc,
  // not a confident point. Perimeter invariant preserved (never the interior).
  const focusMark = focusId ? marks.find((m) => m.id === focusId) : undefined;
  const arcPath = (() => {
    if (!focusMark) return null;
    const n = 28;
    const pts: string[] = [];
    for (let i = 0; i <= n; i++) {
      const a = focusMark.angle - focusMark.spread + (2 * focusMark.spread * i) / n;
      const q = pointOnRim(a);
      pts.push(`${i === 0 ? "M" : "L"}${q.x.toFixed(2)},${q.y.toFixed(2)}`);
    }
    return pts.join(" ");
  })();
  const block = loading && marks.length === 0;
  const aria = block
    ? t.common.loading
    : pendingLabel
      ? pendingLabel
      : "Kostolany egg";

  return (
    <svg
      className={`egg-svg${block ? " is-loading" : ""}`}
      viewBox="-56 -16 500 512"
      role="img"
      aria-label={aria}
      aria-busy={block || Boolean(pendingLabel)}
    >
      <defs>
        <linearGradient id="shell" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#f7f3ea" />
          <stop offset="55%" stopColor="#e7efe4" />
          <stop offset="100%" stopColor="#cfdccf" />
        </linearGradient>
        <radialGradient id="bloom" cx="50%" cy="42%" r="62%">
          <stop offset="0%" stopColor="rgba(255,255,255,0.55)" />
          <stop offset="100%" stopColor="rgba(255,255,255,0)" />
        </radialGradient>
        <filter id="soft">
          <feGaussianBlur stdDeviation="5" />
        </filter>
      </defs>

      <ellipse
        cx={EGG.cx}
        cy={EGG.cy}
        rx={EGG.rx + 10}
        ry={EGG.ry + 12}
        fill="url(#shell)"
        stroke="rgba(30,61,53,0.22)"
        strokeWidth="2"
      />
      <ellipse cx={EGG.cx} cy={EGG.cy} rx={EGG.rx + 10} ry={EGG.ry + 12} fill="url(#bloom)" />

      <ellipse
        cx={EGG.cx}
        cy={EGG.cy}
        rx={EGG.rx}
        ry={EGG.ry}
        fill="none"
        stroke="rgba(30,61,53,0.22)"
        strokeWidth="2"
        strokeDasharray="6 8"
      />

      <g opacity={loading ? 0.28 : 1}>
        {CYCLE.map((code) => {
          const angle = REGIME_ANGLE[code];
          const on = pointOnRim(angle);
          const outer = pointOnRim(angle, EGG.labelRx, EGG.labelRy);
          const guide = REGIME_GUIDE[code];
          const name = t.regimes[code].name;
          const la = labelAnchor(angle);
          return (
            <g key={code}>
              <title>{`${code} ${name}`}</title>
              <circle cx={on.x} cy={on.y} r="3.5" fill={guide.color} opacity="0.75" />
              <line
                x1={on.x}
                y1={on.y}
                x2={outer.x}
                y2={outer.y}
                stroke="rgba(26,31,28,0.18)"
                strokeWidth="1"
              />
              <text
                x={outer.x + la.dx}
                y={outer.y + la.dy}
                textAnchor={la.anchor}
                fontSize="13"
                fontFamily="Fraunces, Georgia, serif"
                fontWeight="700"
                fill={guide.color}
              >
                {code}
              </text>
            </g>
          );
        })}

        {sideBand && SIDE_TIER_OPACITY[sideBand.tier] > 0 && (
          <path
            d={sectorBandPath(SIDE_SECTORS[sideBand.side])}
            fill="none"
            stroke={sideBand.side === "up" ? "#3d9b6e" : "#c45c3e"}
            strokeWidth="13"
            strokeLinecap="round"
            opacity={SIDE_TIER_OPACITY[sideBand.tier]}
          />
        )}
        {zoneCenter && (
          <path
            d={sectorBandPath(zoneSectors(zoneCenter))}
            fill="none"
            stroke="rgba(26,31,28,0.8)"
            strokeWidth="10"
            strokeLinecap="round"
            opacity="0.10"
          />
        )}
        {arcPath && focusMark && (
          <path
            d={arcPath}
            fill="none"
            stroke={focusMark.color}
            strokeWidth="7"
            strokeLinecap="round"
            opacity="0.30"
          />
        )}
        {marks.map((m) => {
          const focused = !focusId || focusId === m.id;
          const r = 7 + m.confidence * 5;
          return (
            <g
              key={m.id}
              opacity={focused ? 1 : 0.55}
              className={`model-pin${onFocus ? " is-interactive" : ""}`}
              role={onFocus ? "button" : undefined}
              tabIndex={onFocus ? 0 : undefined}
              aria-label={m.label}
              onClick={onFocus ? () => onFocus(m.id) : undefined}
              onKeyDown={
                onFocus
                  ? (e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        onFocus(m.id);
                      }
                    }
                  : undefined
              }
            >
              <title>{m.label}</title>
              {/* Hit target + native tooltip */}
              <circle cx={m.x} cy={m.y} r={Math.max(16, r + 4)} fill="transparent" />
              <circle
                className={focused ? "dot-pulse" : undefined}
                cx={m.x}
                cy={m.y}
                r={r}
                fill={m.color}
                opacity="0.2"
                filter="url(#soft)"
              />
              <circle cx={m.x} cy={m.y} r="7.5" fill={m.color} stroke="#fff" strokeWidth="2.5" />
              {/* CSS hover label (visible name) */}
              <g className="model-pin-tip" transform={`translate(${m.x}, ${m.y - 18})`}>
                <rect
                  x={-Math.max(28, m.label.length * 4.2)}
                  y={-14}
                  width={Math.max(56, m.label.length * 8.4)}
                  height={18}
                  rx={6}
                  fill="rgba(26,31,28,0.88)"
                />
                <text
                  x={0}
                  y={-1}
                  textAnchor="middle"
                  fontSize="10"
                  fontWeight="600"
                  fontFamily="IBM Plex Sans, sans-serif"
                  fill="#fff"
                >
                  {m.label}
                </text>
              </g>
            </g>
          );
        })}
      </g>

      {block && (
        <g className="egg-ai-loading" aria-hidden="true">
          <ellipse
            cx={EGG.cx}
            cy={EGG.cy}
            rx={72}
            ry={58}
            fill="rgba(247, 243, 234, 0.72)"
            stroke="rgba(47, 93, 80, 0.2)"
            strokeWidth="1"
          />
          <circle
            className="egg-spin"
            cx={EGG.cx}
            cy={EGG.cy - 12}
            r="14"
            fill="none"
            stroke="#2f5d50"
            strokeWidth="2.5"
            strokeDasharray="22 40"
          />
          <text
            x={EGG.cx}
            y={EGG.cy + 18}
            textAnchor="middle"
            fontFamily="Fraunces, Georgia, serif"
            fontSize="15"
            fontWeight="700"
            fill="#1e3d35"
          >
            {t.common.loading}
          </text>
          <text
            x={EGG.cx}
            y={EGG.cy + 36}
            textAnchor="middle"
            fontFamily="IBM Plex Sans, sans-serif"
            fontSize="10"
            fill="rgba(26,31,28,0.55)"
          >
            {`${t.models.momo.label} · ${t.models.hmm.label} · ${t.models.gbm.label} · ${t.models.tsfm.label}`}
          </text>
        </g>
      )}
      {!block && pendingLabel && (
        <text
          x={EGG.cx}
          y={22}
          textAnchor="middle"
          fontFamily="IBM Plex Sans, sans-serif"
          fontSize="11"
          fill="rgba(26,31,28,0.55)"
        >
          {pendingLabel}
        </text>
      )}
    </svg>
  );
}

/** Static decorative egg for landing (no live models). */
export function LandingEgg() {
  const t = useT();
  const demo: ModelMark[] = [
    {
      id: "hmm",
      label: t.models.hmm.label,
      color: "#2f5d50",
      probabilities: { A2: 0.55, A3: 0.2, A1: 0.1, B1: 0.05, B2: 0.05, B3: 0.05 },
      confidence: 0.55,
    },
  ];
  return <EggChart models={demo} focusId="hmm" />;
}

export type { RegimeCode };
