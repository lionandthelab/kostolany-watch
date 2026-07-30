import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ensureSectorFlow,
  fetchFearGreed,
  fetchFlowCatalog,
  fetchSectorFlow,
  fetchSectorHistory,
  peekSectorFlow,
  startFlowsWarmup,
  type FearGreedGauge,
  type FlowForecast,
  type FlowPoint,
  type HistRange,
  type SectorFlow,
  type SectorGroup,
  type SectorInfo,
} from "./api";

type Props = {
  onBack?: () => void;
  onWatch?: () => void;
  onNews?: () => void;
};

/** A prior scenario and a learned forecast must never look alike in the legend. */
const ARM_KIND_BADGE: Record<string, { label: string; bg: string; fg: string }> = {
  regime_prior: { label: "국면 시나리오", bg: "rgba(196,92,62,0.14)", fg: "#a3492e" },
  learned: { label: "학습 예측", bg: "rgba(47,93,80,0.14)", fg: "#2f5d50" },
};

const BADGE_BASE = {
  marginLeft: 6,
  padding: "1px 7px",
  borderRadius: 999,
  fontSize: "0.72em",
  fontWeight: 600,
  whiteSpace: "nowrap" as const,
};

const HIST_RANGE_OPTIONS: { id: HistRange; label: string }[] = [
  { id: "6m", label: "6개월" },
  { id: "1y", label: "1년" },
  { id: "3y", label: "3년" },
  { id: "5y", label: "5년" },
];

function toPath(
  points: FlowPoint[],
  x0: number,
  x1: number,
  y0: number,
  y1: number,
  vMin: number,
  vMax: number,
): string {
  if (!points.length) return "";
  const span = Math.max(1e-6, vMax - vMin);
  return points
    .map((p, i) => {
      const x = x0 + ((x1 - x0) * i) / Math.max(1, points.length - 1);
      const y = y1 - ((p.value - vMin) / span) * (y1 - y0);
      return `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
}

/**
 * Shaded q10–q90 cone for one arm. Only the terminal band is a model claim; the
 * taper hugs that arm's own path and widens with sqrt(time) — the plain
 * random-walk shape — so no intermediate quantile is invented. Returns "" when
 * the arm produced no band, which is the correct outcome for a prior scenario.
 */
function toBandPath(
  f: FlowForecast,
  x0: number,
  x1: number,
  y0: number,
  y1: number,
  vMin: number,
  vMax: number,
): string {
  const band = f.band;
  const n = f.points.length;
  if (!band || !n) return "";
  const endVal = f.points[n - 1].value;
  const hi: number[] = [100];
  const lo: number[] = [100];
  for (let i = 0; i < n; i++) {
    const w = Math.sqrt((i + 1) / n);
    hi.push(f.points[i].value + (band.q90 - endVal) * w);
    lo.push(f.points[i].value + (band.q10 - endVal) * w);
  }
  const span = Math.max(1e-6, vMax - vMin);
  const xAt = (i: number) => x0 + ((x1 - x0) * i) / Math.max(1, hi.length - 1);
  const yAt = (v: number) => y1 - ((v - vMin) / span) * (y1 - y0);
  const up = hi
    .map((v, i) => `${i === 0 ? "M" : "L"}${xAt(i).toFixed(1)},${yAt(v).toFixed(1)}`)
    .join(" ");
  let down = "";
  for (let i = lo.length - 1; i >= 0; i--) {
    down += ` L${xAt(i).toFixed(1)},${yAt(lo[i]).toFixed(1)}`;
  }
  return `${up}${down} Z`;
}

function fmtAxisDate(iso: string): string {
  if (!iso) return "";
  const m = iso.match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (!m) return iso.slice(0, 10);
  return `${m[1].slice(2)}.${m[2]}.${m[3]}`;
}

function FlowChart({
  history,
  forecasts,
  asof,
  forecastLoading = false,
}: {
  history: FlowPoint[];
  forecasts: FlowForecast[];
  asof: string;
  forecastLoading?: boolean;
}) {
  const W = 720;
  const H = 360;
  const pad = { l: 44, r: 18, t: 28, b: 48 };
  const splitX = pad.l + (W - pad.l - pad.r) * 0.62;

  const allVals = useMemo(() => {
    const vals = history.map((p) => p.value);
    for (const f of forecasts) {
      for (const p of f.points) vals.push(p.value);
      // Bands are honest and wide; let them set the scale rather than clip.
      if (f.band) vals.push(f.band.q10, f.band.q90);
    }
    return vals;
  }, [history, forecasts]);

  const vMin = Math.min(...(allVals.length ? allVals : [100]), 92) - 2;
  const vMax = Math.max(...(allVals.length ? allVals : [100]), 108) + 2;

  const histPath = toPath(history, pad.l, splitX, pad.t, H - pad.b, vMin, vMax);
  const baseY = H - pad.b - ((100 - vMin) / Math.max(1e-6, vMax - vMin)) * (H - pad.b - pad.t);

  const histStart = history[0]?.date ?? "";
  const histMid = history[Math.floor(history.length / 2)]?.date ?? "";
  const fwdEnd =
    !forecastLoading && forecasts[0]?.points?.length
      ? forecasts[0].points[forecasts[0].points.length - 1]?.date
      : "";

  return (
    <svg className="flow-svg" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="섹터 흐름 그래프">
      <defs>
        <linearGradient id="histFill" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="rgba(47,93,80,0.22)" />
          <stop offset="100%" stopColor="rgba(47,93,80,0)" />
        </linearGradient>
      </defs>

      {[0, 0.25, 0.5, 0.75, 1].map((t) => {
        const y = pad.t + (H - pad.b - pad.t) * t;
        const val = vMax - (vMax - vMin) * t;
        return (
          <g key={t}>
            <line x1={pad.l} x2={W - pad.r} y1={y} y2={y} stroke="rgba(26,31,28,0.08)" />
            <text x={pad.l - 8} y={y + 4} textAnchor="end" fontSize="10" fill="rgba(26,31,28,0.45)">
              {val.toFixed(0)}
            </text>
          </g>
        );
      })}

      <line
        x1={pad.l}
        x2={W - pad.r}
        y1={baseY}
        y2={baseY}
        stroke="rgba(26,31,28,0.2)"
        strokeDasharray="4 4"
      />

      <g className="flow-lines">
        {histPath && (
          <>
            <path
              className="flow-line"
              d={`${histPath} L${splitX},${H - pad.b} L${pad.l},${H - pad.b} Z`}
              fill="url(#histFill)"
            />
            <path
              className="flow-line"
              d={histPath}
              fill="none"
              stroke="#2f5d50"
              strokeWidth="2.4"
            />
          </>
        )}

        {!forecastLoading &&
          forecasts.map((f) => {
            const d = toBandPath(f, splitX, W - pad.r, pad.t, H - pad.b, vMin, vMax);
            if (!d) return null;
            return (
              <path key={`band-${f.id}`} d={d} fill={f.color} opacity={0.14} stroke="none" />
            );
          })}

        {!forecastLoading &&
          forecasts.map((f) => {
            const pts = [{ date: asof, value: 100 }, ...f.points];
            const d = toPath(pts, splitX, W - pad.r, pad.t, H - pad.b, vMin, vMax);
            return (
              <path
                key={f.id}
                className="flow-line"
                d={d}
                fill="none"
                stroke={f.color}
                strokeWidth="2.2"
                strokeDasharray="6 4"
                opacity={0.95}
              />
            );
          })}
      </g>

      <line x1={splitX} x2={splitX} y1={pad.t} y2={H - pad.b} stroke="#c45c3e" strokeWidth="1.5" />
      <text x={splitX + 6} y={pad.t + 12} fontSize="11" fill="#c45c3e" fontWeight="600">
        오늘 · {fmtAxisDate(asof)}
      </text>

      {forecastLoading && (
        <g className="flow-forecast-pending">
          <rect
            x={splitX}
            y={pad.t}
            width={W - pad.r - splitX}
            height={H - pad.b - pad.t}
            fill="rgba(244, 241, 234, 0.55)"
          />
          <text
            x={(splitX + W - pad.r) / 2}
            y={(pad.t + H - pad.b) / 2}
            textAnchor="middle"
            fontSize="13"
            fill="rgba(26,31,28,0.55)"
            fontWeight="600"
          >
            3개월 시나리오 준비 중…
          </text>
        </g>
      )}

      {/* X-axis date labels */}
      <line
        x1={pad.l}
        x2={W - pad.r}
        y1={H - pad.b}
        y2={H - pad.b}
        stroke="rgba(26,31,28,0.18)"
      />
      {histStart && (
        <text x={pad.l} y={H - pad.b + 16} fontSize="10" fill="rgba(26,31,28,0.55)">
          {fmtAxisDate(histStart)}
        </text>
      )}
      {histMid && (
        <text
          x={(pad.l + splitX) / 2}
          y={H - pad.b + 16}
          textAnchor="middle"
          fontSize="10"
          fill="rgba(26,31,28,0.55)"
        >
          {fmtAxisDate(histMid)}
        </text>
      )}
      {asof && (
        <text
          x={splitX}
          y={H - pad.b + 16}
          textAnchor="middle"
          fontSize="10"
          fill="rgba(26,31,28,0.55)"
        >
          {fmtAxisDate(asof)}
        </text>
      )}
      {fwdEnd ? (
        <text
          x={W - pad.r}
          y={H - pad.b + 16}
          textAnchor="end"
          fontSize="10"
          fill="rgba(26,31,28,0.55)"
        >
          {fmtAxisDate(fwdEnd)}
        </text>
      ) : (
        <text
          x={W - pad.r}
          y={H - pad.b + 16}
          textAnchor="end"
          fontSize="10"
          fill="rgba(26,31,28,0.4)"
        >
          +3개월
        </text>
      )}
      <text x={pad.l} y={H - 8} fontSize="10" fill="rgba(26,31,28,0.4)">
        실데이터
      </text>
      <text x={splitX + 8} y={H - 8} fontSize="10" fill="rgba(26,31,28,0.4)">
        AI 3개월
      </text>
    </svg>
  );
}

function FearGreedHistoryChart({ series }: { series: FlowPoint[] }) {
  const W = 720;
  const H = 180;
  const pad = { l: 36, r: 16, t: 16, b: 32 };
  const vMin = 0;
  const vMax = 100;
  const path = toPath(series, pad.l, W - pad.r, pad.t, H - pad.b, vMin, vMax);
  const start = series[0]?.date ?? "";
  const end = series[series.length - 1]?.date ?? "";
  const mid = series[Math.floor(series.length / 2)]?.date ?? "";
  const midY = pad.t + ((vMax - 50) / (vMax - vMin)) * (H - pad.b - pad.t);

  return (
    <svg className="fg-history-svg" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="공탐 지수 추이">
      <line x1={pad.l} x2={W - pad.r} y1={midY} y2={midY} stroke="rgba(26,31,28,0.15)" strokeDasharray="4 3" />
      <text x={pad.l - 6} y={midY + 3} textAnchor="end" fontSize="9" fill="rgba(26,31,28,0.4)">
        50
      </text>
      {path && <path d={path} fill="none" stroke="#2f5d50" strokeWidth="2" className="flow-line" />}
      <line x1={pad.l} x2={W - pad.r} y1={H - pad.b} y2={H - pad.b} stroke="rgba(26,31,28,0.18)" />
      {start && (
        <text x={pad.l} y={H - 10} fontSize="10" fill="rgba(26,31,28,0.5)">
          {fmtAxisDate(start)}
        </text>
      )}
      {mid && (
        <text x={(pad.l + W - pad.r) / 2} y={H - 10} textAnchor="middle" fontSize="10" fill="rgba(26,31,28,0.5)">
          {fmtAxisDate(mid)}
        </text>
      )}
      {end && (
        <text x={W - pad.r} y={H - 10} textAnchor="end" fontSize="10" fill="rgba(26,31,28,0.5)">
          {fmtAxisDate(end)}
        </text>
      )}
    </svg>
  );
}

export default function FlowsDesk({ onBack, onWatch, onNews }: Props) {
  const [sectors, setSectors] = useState<SectorInfo[]>([]);
  const [groups, setGroups] = useState<SectorGroup[]>([]);
  const [groupId, setGroupId] = useState("markets");
  const [sectorId, setSectorId] = useState("spx");
  const [flow, setFlow] = useState<SectorFlow | null>(null);
  const [gauge, setGauge] = useState<FearGreedGauge | null>(null);
  const [fgOpen, setFgOpen] = useState(false);
  const [histRange, setHistRange] = useState<HistRange>("1y");
  const [loading, setLoading] = useState(true);
  const [forecastPending, setForecastPending] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const memRef = useRef<Map<string, SectorFlow>>(new Map());
  const pollRef = useRef<number | null>(null);
  const reqGen = useRef(0);
  const sectorIdRef = useRef(sectorId);
  sectorIdRef.current = sectorId;

  useEffect(() => {
    void fetchFlowCatalog()
      .then((cat) => {
        setSectors(cat.sectors);
        setGroups(cat.groups);
        if (cat.groups[0]) setGroupId(cat.groups[0].id);
        void startFlowsWarmup(false).catch(() => undefined);
      })
      .catch(() => {
        setSectors([]);
        setGroups([]);
      });
    void fetchFearGreed(false)
      .then(setGauge)
      .catch(() => undefined);
  }, []);

  const activeGroup = groups.find((g) => g.id === groupId) ?? groups[0];
  const groupSectors = useMemo(() => {
    if (!activeGroup) return sectors;
    const ids = new Set(activeGroup.sector_ids);
    const filtered = sectors.filter((s) => ids.has(s.id));
    return filtered.length ? filtered : sectors;
  }, [activeGroup, sectors]);

  useEffect(() => {
    if (!groupSectors.length) return;
    if (!groupSectors.some((s) => s.id === sectorId)) {
      setSectorId(groupSectors[0].id);
    }
  }, [groupSectors, sectorId]);

  const stopPoll = useCallback(() => {
    if (pollRef.current != null) {
      window.clearTimeout(pollRef.current);
      window.clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const applyFlow = useCallback((id: string, data: SectorFlow) => {
    memRef.current.set(id, data);
    if (id === sectorIdRef.current) {
      setFlow(data);
      const pending =
        Boolean(data.forecast_pending) || !(data.forecasts && data.forecasts.length);
      setForecastPending(pending);
    }
  }, []);

  const applyHistoryOnly = useCallback((id: string, data: SectorFlow) => {
    const existing = memRef.current.get(id);
    // Keep AI forecasts if already loaded; only swap the visible history window.
    if (existing && existing.forecasts && existing.forecasts.length) {
      const merged: SectorFlow = {
        ...existing,
        history: data.history,
        asof: data.asof || existing.asof,
        hist_range: data.hist_range ?? existing.hist_range,
        hist_range_label: data.hist_range_label ?? existing.hist_range_label,
      };
      memRef.current.set(id, merged);
      if (id === sectorIdRef.current) {
        setFlow(merged);
        setForecastPending(false);
      }
      return;
    }
    const partial: SectorFlow = {
      ...data,
      forecasts: [],
      consensus: null,
      forecast_pending: true,
    };
    memRef.current.set(id, partial);
    if (id === sectorIdRef.current) {
      setFlow(partial);
      setForecastPending(true);
    }
  }, []);

  const pollFresh = useCallback(
    (id: string, prevBuilt?: string) => {
      stopPoll();
      if (id === sectorIdRef.current) setRefreshing(true);
      let ticks = 0;
      let delay = 3000;
      const tick = () => {
        ticks += 1;
        if (id !== sectorIdRef.current) {
          stopPoll();
          return;
        }
        void peekSectorFlow(id).then((next) => {
          if (id !== sectorIdRef.current) return;
          if (next.status === "busy") {
            delay = Math.min(20000, delay * 1.5);
          } else if (next.status === "hit") {
            const data = next.data;
            const newer = Boolean(data.built_at && data.built_at !== prevBuilt);
            const done = !data.refreshing && (!data.stale || newer);
            if (newer || done) applyFlow(id, data);
            if (done || ticks >= 40) {
              setRefreshing(false);
              stopPoll();
              return;
            }
            delay = 3000;
          }
          pollRef.current = window.setTimeout(tick, delay);
        });
      };
      pollRef.current = window.setTimeout(tick, delay);
    },
    [applyFlow, stopPoll],
  );

  const histRangeRef = useRef(histRange);
  histRangeRef.current = histRange;

  const load = useCallback(
    async (id: string, refresh = false) => {
      const gen = ++reqGen.current;
      const range = histRangeRef.current;
      stopPoll();
      const mem = memRef.current.get(id);
      if (mem) {
        applyFlow(id, mem);
        setLoading(false);
        setForecastPending(!(mem.forecasts && mem.forecasts.length));
      } else {
        setForecastPending(true);
        setLoading(true);
      }
      setError(null);
      setRefreshing(false);

      const paintHistorySoon = () => {
        void fetchSectorHistory(id, refresh, range)
          .then((hist) => {
            if (gen !== reqGen.current || id !== sectorIdRef.current) return;
            if (histRangeRef.current !== range) return;
            applyHistoryOnly(id, hist);
            setLoading(false);
          })
          .catch(() => undefined);
      };

      try {
        if (refresh) {
          paintHistorySoon();
          void fetchSectorFlow(id, true).then((data) => {
            if (gen !== reqGen.current || id !== sectorIdRef.current) return;
            if (data) {
              applyFlow(id, data);
              setLoading(false);
            }
          });
          void startFlowsWarmup(false).catch(() => undefined);
          pollFresh(id, mem?.built_at);
          return;
        }

        paintHistorySoon();

        const peeked = await peekSectorFlow(id);
        if (gen !== reqGen.current || id !== sectorIdRef.current) return;

        if (peeked.status === "hit" && peeked.data.forecasts?.length) {
          applyFlow(id, peeked.data);
          setLoading(false);
          if (peeked.data.refreshing || peeked.data.stale) {
            pollFresh(id, peeked.data.built_at);
          }
          return;
        }

        const cached = await fetchSectorFlow(id, false).catch(() => null);
        if (gen !== reqGen.current || id !== sectorIdRef.current) return;
        if (cached && cached.forecasts?.length) {
          applyFlow(id, cached);
          setLoading(false);
          if (cached.refreshing || cached.stale) {
            pollFresh(id, cached.built_at);
          }
          return;
        }

        void ensureSectorFlow(id).catch(() => undefined);
        void startFlowsWarmup(false).catch(() => undefined);
        setForecastPending(true);

        let delay = peeked.status === "busy" ? 3500 : 1800;
        let ticks = 0;
        const waitOnce = () => {
          ticks += 1;
          if (gen !== reqGen.current || id !== sectorIdRef.current) return;
          void peekSectorFlow(id).then((again) => {
            if (gen !== reqGen.current || id !== sectorIdRef.current) return;
            if (again.status === "hit" && again.data.forecasts?.length) {
              applyFlow(id, again.data);
              setLoading(false);
              if (again.data.refreshing || again.data.stale) {
                pollFresh(id, again.data.built_at);
              }
              return;
            }
            if (again.status === "busy") {
              delay = Math.min(12000, Math.round(delay * 1.35));
            } else if (again.status === "miss" && ticks % 4 === 0) {
              void ensureSectorFlow(id).catch(() => undefined);
            }
            if (ticks >= 90) {
              setLoading(false);
              if (!memRef.current.get(id)?.history?.length) {
                setError("불러오지 못했습니다.");
              }
              return;
            }
            pollRef.current = window.setTimeout(waitOnce, delay);
          });
        };
        pollRef.current = window.setTimeout(waitOnce, delay);
      } catch (e) {
        if (gen !== reqGen.current || id !== sectorIdRef.current) return;
        if (!memRef.current.get(id)?.history?.length) {
          setError(e instanceof Error ? e.message : String(e));
        }
        setLoading(false);
        setRefreshing(false);
      }
    },
    [applyFlow, applyHistoryOnly, pollFresh, stopPoll],
  );

  useEffect(() => {
    void load(sectorId, false);
    return () => stopPoll();
  }, [sectorId, load, stopPoll]);

  // Range badge: swap history lines only (keep forecasts / chart frame)
  useEffect(() => {
    const id = sectorId;
    const range = histRange;
    void fetchSectorHistory(id, false, range)
      .then((hist) => {
        if (id !== sectorIdRef.current || histRangeRef.current !== range) return;
        applyHistoryOnly(id, hist);
      })
      .catch(() => undefined);
  }, [histRange, sectorId, applyHistoryOnly]);

  // Keep last painted chart when switching — avoid unmount flash
  const displayFlow = flow;
  const chartMatches = displayFlow?.sector.id === sectorId;
  const showForecastPending = forecastPending || !chartMatches;

  return (
    <div className="page flows-page">
      <nav className="topnav desk-nav">
        {onBack && (
          <button type="button" className="nav-quiet nav-btn" onClick={onBack}>
            ← 소개
          </button>
        )}
        <div className="desk-tabs" role="tablist" aria-label="화면">
          <button type="button" className="desk-tab" onClick={onWatch}>
            국면
          </button>
          <button type="button" className="desk-tab" onClick={onNews}>
            뉴스
          </button>
          <button type="button" className="desk-tab is-active" aria-current="page">
            흐름
          </button>
        </div>
      </nav>

      <header className="news-hero fade-up">
        <div className="flows-hero-row">
          <h2 className="section-kicker">흐름</h2>
          {gauge && (
            <button
              type="button"
              className={`fg-chip${fgOpen ? " is-open" : ""}`}
              onClick={() => setFgOpen((o) => !o)}
              aria-expanded={fgOpen}
              title="공탐 지수 추이 보기"
            >
              <span className="fg-chip-label">공탐</span>
              <strong className="fg-chip-num">{Math.round(gauge.score)}</strong>
              <span className="fg-chip-tag">{gauge.label}</span>
            </button>
          )}
        </div>
        <div className="cache-bar">
          <span className="status">
            {displayFlow
              ? `${displayFlow.asof} · ${
                  chartMatches ? displayFlow.sector.symbol : sectorId
                }${showForecastPending ? " · 예측 로딩" : ""}`
              : loading
                ? "불러오는 중…"
                : ""}
          </span>
          <button
            type="button"
            className="btn-refresh"
            disabled={loading && !displayFlow}
            onClick={() => {
              void load(sectorId, true);
              void fetchFearGreed(true)
                .then(setGauge)
                .catch(() => undefined);
            }}
          >
            {refreshing || forecastPending ? "갱신 중" : "새로고침"}
          </button>
        </div>
      </header>

      {fgOpen && gauge && (
        <section className="fg-history-panel fade-up" aria-label="공탐 지수 추이">
          <div className="fg-history-head">
            <h3>공탐 지수 추이</h3>
            <p>
              {gauge.asof ? `기준 ${gauge.asof}` : ""}
              {gauge.components?.vix != null ? ` · VIX ${gauge.components.vix}` : ""}
            </p>
          </div>
          {gauge.series && gauge.series.length > 2 ? (
            <FearGreedHistoryChart series={gauge.series} />
          ) : (
            <p className="status">추이 데이터를 불러오는 중이거나 없습니다.</p>
          )}
          <p className="fg-history-note">{gauge.disclaimer}</p>
        </section>
      )}

      <div className="flow-group-tabs" role="tablist" aria-label="분류">
        {(groups.length
          ? groups
          : [{ id: "markets", label_ko: "시장·국가", sector_ids: ["spx"] }]
        ).map((g) => (
          <button
            key={g.id}
            type="button"
            className={`flow-group-tab${groupId === g.id ? " is-active" : ""}`}
            onClick={() => setGroupId(g.id)}
          >
            {g.label_ko}
          </button>
        ))}
      </div>

      <div className="news-filters" role="tablist" aria-label="티커">
        {(groupSectors.length ? groupSectors : [{ id: "spx", label: "S&P 500", blurb: "SPY" }]).map(
          (s) => (
            <button
              key={s.id}
              type="button"
              className={`news-filter${sectorId === s.id ? " is-active" : ""}`}
              onClick={() => setSectorId(s.id)}
            >
              {s.label}
              {s.symbol ? <span className="filter-sym">{s.symbol}</span> : null}
            </button>
          ),
        )}
      </div>

      {error && (
        <p className="status">
          오류: {error}{" "}
          <button type="button" className="linkish" onClick={() => void load(sectorId, false)}>
            다시 시도
          </button>
        </p>
      )}

      {/* Stable chart shell — never remount on sector badge change */}
      <section className="flow-panel fade-up">
        <div className="flow-panel-head">
          <div>
            <h2 className="news-section-title">
              {chartMatches
                ? displayFlow?.sector.label
                : groupSectors.find((s) => s.id === sectorId)?.label || sectorId}{" "}
              <span className="flow-sym">
                {chartMatches
                  ? displayFlow?.sector.symbol
                  : groupSectors.find((s) => s.id === sectorId)?.symbol || ""}
              </span>
            </h2>
            <div className="flow-range-tabs" role="tablist" aria-label="실데이터 기간">
              {HIST_RANGE_OPTIONS.map((r) => (
                <button
                  key={r.id}
                  type="button"
                  className={`flow-range-tab${histRange === r.id ? " is-active" : ""}`}
                  onClick={() => setHistRange(r.id)}
                >
                  {r.label}
                </button>
              ))}
            </div>
          </div>
          {displayFlow?.consensus && chartMatches && !forecastPending ? (
            <div className={`flow-consensus is-${displayFlow.consensus.outlook}`}>
              <span>3개월</span>
              <strong>
                {displayFlow.consensus.outlook === "up" ? "Up" : displayFlow.consensus.outlook === "down" ? "Down" : "Flat"}{" "}
                {displayFlow.consensus.change_pct > 0 ? "+" : ""}
                {displayFlow.consensus.change_pct}%
              </strong>
            </div>
          ) : (
            <div className="flow-consensus is-pending">
              <span>3개월</span>
              <strong>준비 중…</strong>
            </div>
          )}
        </div>

        {displayFlow?.history?.length ? (
          <FlowChart
            history={displayFlow.history}
            forecasts={chartMatches && !forecastPending ? displayFlow.forecasts : []}
            asof={displayFlow.asof}
            forecastLoading={showForecastPending}
          />
        ) : (
          <div className="flow-svg flow-svg-placeholder" aria-busy="true">
            <p className="status">실데이터 불러오는 중…</p>
          </div>
        )}

        <ul className="flow-legend">
          <li>
            <i style={{ background: "#2f5d50" }} />
            실데이터
          </li>
          {showForecastPending || !chartMatches ? (
            <li className="flow-legend-pending">AI 3개월 시나리오 로딩 중…</li>
          ) : (
            displayFlow?.forecasts.map((f) => {
              const badge = f.arm_kind ? ARM_KIND_BADGE[f.arm_kind] : undefined;
              return (
                <li key={f.id}>
                  <i style={{ background: f.color }} />
                  {f.label} · {f.outlook === "up" ? "Up" : f.outlook === "down" ? "Down" : "Flat"} {f.change_pct > 0 ? "+" : ""}
                  {f.change_pct}% · {f.regime}
                  {badge && (
                    <span style={{ ...BADGE_BASE, background: badge.bg, color: badge.fg }}>
                      {badge.label}
                    </span>
                  )}
                  {f.band && (
                    <span
                      style={{
                        ...BADGE_BASE,
                        background: "transparent",
                        color: "rgba(26,31,28,0.5)",
                        fontWeight: 500,
                      }}
                    >
                      음영 = q10~q90
                    </span>
                  )}
                </li>
              );
            })
          )}
        </ul>

        {(() => {
          // P(up) only means something beside the asset's own base rate.
          if (showForecastPending || !chartMatches || !displayFlow) return null;
          const arm = displayFlow.forecasts.find((f) => typeof f.p_up === "number");
          if (!arm || typeof arm.p_up !== "number") return null;
          const base = displayFlow.base_rate_up;
          const n = displayFlow.base_rate_n;
          return (
            <div
              className="flow-pup-row"
              style={{
                display: "flex",
                flexWrap: "wrap",
                alignItems: "center",
                gap: 10,
                marginTop: 4,
              }}
            >
              <span
                style={{
                  ...BADGE_BASE,
                  marginLeft: 0,
                  fontSize: "0.86rem",
                  padding: "3px 10px",
                  background: "rgba(74,124,155,0.16)",
                  color: "#31607d",
                }}
              >
                {arm.label} 상승 확률 {Math.round(arm.p_up * 100)}%
              </span>
              <span className="status" style={{ margin: 0 }}>
                {typeof base === "number"
                  ? `과거 3개월 상승 비율 ${Math.round(base * 100)}%${
                      n ? ` (겹치는 ${n}봉 기준)` : ""
                    }`
                  : "과거 상승 비율 없음"}
              </span>
            </div>
          );
        })()}
      </section>

      {displayFlow && <p className="disclaimer">{displayFlow.disclaimer}</p>}
    </div>
  );
}
