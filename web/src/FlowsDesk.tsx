import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  fetchFlowCatalog,
  fetchSectorFlow,
  peekSectorFlow,
  startFlowsWarmup,
  type FlowForecast,
  type FlowPoint,
  type SectorFlow,
  type SectorGroup,
  type SectorInfo,
} from "./api";

type Props = {
  onBack?: () => void;
  onWatch?: () => void;
  onNews?: () => void;
};

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

function FlowChart({
  history,
  forecasts,
  asof,
}: {
  history: FlowPoint[];
  forecasts: FlowForecast[];
  asof: string;
}) {
  const W = 720;
  const H = 340;
  const pad = { l: 44, r: 18, t: 28, b: 36 };
  const splitX = pad.l + (W - pad.l - pad.r) * 0.62;

  const allVals = useMemo(() => {
    const vals = history.map((p) => p.value);
    for (const f of forecasts) for (const p of f.points) vals.push(p.value);
    return vals;
  }, [history, forecasts]);

  const vMin = Math.min(...allVals, 92) - 2;
  const vMax = Math.max(...allVals, 108) + 2;

  const histPath = toPath(history, pad.l, splitX, pad.t, H - pad.b, vMin, vMax);
  const baseY = H - pad.b - ((100 - vMin) / Math.max(1e-6, vMax - vMin)) * (H - pad.b - pad.t);

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

      {histPath && (
        <>
          <path
            d={`${histPath} L${splitX},${H - pad.b} L${pad.l},${H - pad.b} Z`}
            fill="url(#histFill)"
          />
          <path d={histPath} fill="none" stroke="#2f5d50" strokeWidth="2.4" />
        </>
      )}

      <line x1={splitX} x2={splitX} y1={pad.t} y2={H - pad.b} stroke="#c45c3e" strokeWidth="1.5" />
      <text x={splitX + 6} y={pad.t + 12} fontSize="11" fill="#c45c3e" fontWeight="600">
        오늘 · {asof}
      </text>
      <text x={pad.l} y={H - 12} fontSize="11" fill="rgba(26,31,28,0.5)">
        실데이터
      </text>
      <text x={splitX + 8} y={H - 12} fontSize="11" fill="rgba(26,31,28,0.5)">
        AI 3개월
      </text>

      {forecasts.map((f) => {
        const pts = [{ date: asof, value: 100 }, ...f.points];
        const d = toPath(pts, splitX, W - pad.r, pad.t, H - pad.b, vMin, vMax);
        return (
          <path
            key={f.id}
            d={d}
            fill="none"
            stroke={f.color}
            strokeWidth="2.2"
            strokeDasharray="6 4"
            opacity={0.95}
          />
        );
      })}
    </svg>
  );
}

export default function FlowsDesk({ onBack, onWatch, onNews }: Props) {
  const [sectors, setSectors] = useState<SectorInfo[]>([]);
  const [groups, setGroups] = useState<SectorGroup[]>([]);
  const [groupId, setGroupId] = useState("markets");
  const [sectorId, setSectorId] = useState("kospi");
  const [flow, setFlow] = useState<SectorFlow | null>(null);
  const [loading, setLoading] = useState(true);
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
      window.clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const applyFlow = useCallback((id: string, data: SectorFlow) => {
    memRef.current.set(id, data);
    // Never paint a stale sector over the user's current selection
    if (id === sectorIdRef.current) {
      setFlow(data);
    }
  }, []);

  const pollFresh = useCallback(
    (id: string, prevBuilt?: string) => {
      stopPoll();
      if (id === sectorIdRef.current) setRefreshing(true);
      let ticks = 0;
      pollRef.current = window.setInterval(() => {
        ticks += 1;
        if (id !== sectorIdRef.current) {
          stopPoll();
          return;
        }
        void peekSectorFlow(id).then((next) => {
          if (next.status !== "hit" || id !== sectorIdRef.current) return;
          const data = next.data;
          const newer = Boolean(data.built_at && data.built_at !== prevBuilt);
          const done = !data.refreshing && (!data.stale || newer);
          if (newer || done) {
            applyFlow(id, data);
          }
          if (done || ticks >= 40) {
            setRefreshing(false);
            stopPoll();
          }
        });
      }, 2500);
    },
    [applyFlow, stopPoll],
  );

  const load = useCallback(
    async (id: string, refresh = false) => {
      const gen = ++reqGen.current;
      stopPoll();
      const mem = memRef.current.get(id);
      if (mem) {
        applyFlow(id, mem);
        setLoading(false);
      } else {
        // Drop previous sector chart so badge clicks feel immediate
        setFlow(null);
        setLoading(true);
      }
      setError(null);
      setRefreshing(false);

      try {
        if (!refresh) {
          const peeked = await peekSectorFlow(id);
          if (gen !== reqGen.current || id !== sectorIdRef.current) return;
          if (peeked.status === "hit") {
            applyFlow(id, peeked.data);
            setLoading(false);
            if (peeked.data.refreshing || peeked.data.stale) {
              pollFresh(id, peeked.data.built_at);
            }
            // Still soft-refresh in background without blocking UI
            void fetchSectorFlow(id, false)
              .then((data) => {
                if (gen !== reqGen.current || id !== sectorIdRef.current) return;
                applyFlow(id, data);
                if (data.refreshing || data.stale) pollFresh(id, data.built_at);
              })
              .catch(() => undefined);
            return;
          }

          // busy: only poll — do not stampede compute/warmup under 429
          // miss: kick warmup once, then poll; fall back to one soft fetch
          if (peeked.status === "miss") {
            void startFlowsWarmup(false).catch(() => undefined);
            void fetchSectorFlow(id, false)
              .then((data) => {
                if (gen !== reqGen.current || id !== sectorIdRef.current) return;
                applyFlow(id, data);
                setLoading(false);
                if (data.refreshing || data.stale) pollFresh(id, data.built_at);
              })
              .catch((e) => {
                if (gen !== reqGen.current || id !== sectorIdRef.current) return;
                if (!memRef.current.get(id)) {
                  setError(e instanceof Error ? e.message : String(e));
                }
                setLoading(false);
              });
          }

          let ticks = 0;
          const waitId = window.setInterval(() => {
            ticks += 1;
            if (gen !== reqGen.current || id !== sectorIdRef.current) {
              window.clearInterval(waitId);
              return;
            }
            void peekSectorFlow(id).then((again) => {
              if (again.status !== "hit" || gen !== reqGen.current || id !== sectorIdRef.current) {
                return;
              }
              applyFlow(id, again.data);
              setLoading(false);
              window.clearInterval(waitId);
              if (again.data.refreshing || again.data.stale) pollFresh(id, again.data.built_at);
            });
            if (ticks >= 90) {
              window.clearInterval(waitId);
              if (peeked.status === "busy") {
                void fetchSectorFlow(id, false)
                  .then((data) => {
                    if (gen !== reqGen.current || id !== sectorIdRef.current) return;
                    applyFlow(id, data);
                    setLoading(false);
                  })
                  .catch((e) => {
                    if (gen !== reqGen.current || id !== sectorIdRef.current) return;
                    if (!memRef.current.get(id)) {
                      setError(e instanceof Error ? e.message : String(e));
                    }
                    setLoading(false);
                  });
              }
            }
          }, peeked.status === "busy" ? 4000 : 2000);
          return;
        }

        // Explicit refresh: keep current chart, refresh in background
        const data = await fetchSectorFlow(id, true);
        if (gen !== reqGen.current || id !== sectorIdRef.current) return;
        applyFlow(id, data);
        setLoading(false);
        if (data.refreshing || data.stale || refresh) {
          pollFresh(id, data.built_at);
        } else {
          setRefreshing(false);
        }
      } catch (e) {
        if (gen !== reqGen.current || id !== sectorIdRef.current) return;
        if (!memRef.current.get(id)) {
          setError(e instanceof Error ? e.message : String(e));
        }
        setLoading(false);
        setRefreshing(false);
      }
    },
    [applyFlow, pollFresh, stopPoll],
  );

  useEffect(() => {
    void load(sectorId, false);
    return () => stopPoll();
  }, [sectorId, load, stopPoll]);

  useEffect(() => {
    // Prefetch lightly to avoid Cloud Run rate limits during warmup
    for (const s of groupSectors.slice(0, 4)) {
      if (memRef.current.has(s.id)) continue;
      void peekSectorFlow(s.id).then((p) => {
        if (p.status === "hit") memRef.current.set(s.id, p.data);
      });
    }
  }, [groupSectors]);

  const visibleFlow = flow && flow.sector.id === sectorId ? flow : null;
  const statusLabel =
    loading && !visibleFlow
      ? "준비 중…"
      : refreshing
        ? "백그라운드 갱신 중…"
        : visibleFlow?.stale
          ? "캐시(갱신 대기)"
          : visibleFlow?.cached
            ? "캐시"
            : "최신";

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
        <h2 className="section-kicker">흐름</h2>
        <div className="cache-bar">
          <span className="status">
            {statusLabel}
            {visibleFlow ? ` · ${visibleFlow.asof}` : ""}
            {visibleFlow ? ` · ${visibleFlow.sector.symbol}` : ""}
          </span>
          <button
            type="button"
            className="btn-refresh"
            disabled={refreshing && !visibleFlow}
            onClick={() => void load(sectorId, true)}
          >
            {refreshing ? "갱신 중" : "새로고침"}
          </button>
        </div>
      </header>

      <div className="flow-group-tabs" role="tablist" aria-label="분류">
        {(groups.length
          ? groups
          : [{ id: "markets", label_ko: "시장·국가", sector_ids: ["kospi"] }]
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
        {(groupSectors.length ? groupSectors : [{ id: "kospi", label: "코스피", blurb: "KS11" }]).map(
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

      {visibleFlow && (
        <>
          <section className="flow-panel fade-up" key={visibleFlow.sector.id}>
            <div className="flow-panel-head">
              <div>
                <h2 className="news-section-title">
                  {visibleFlow.sector.label}{" "}
                  <span className="flow-sym">{visibleFlow.sector.symbol}</span>
                </h2>
              </div>
              <div className={`flow-consensus is-${visibleFlow.consensus.outlook}`}>
                <span>3개월</span>
                <strong>
                  {visibleFlow.consensus.outlook === "up" ? "Up" : "Down"}{" "}
                  {visibleFlow.consensus.change_pct > 0 ? "+" : ""}
                  {visibleFlow.consensus.change_pct}%
                </strong>
              </div>
            </div>

            <FlowChart
              history={visibleFlow.history}
              forecasts={visibleFlow.forecasts}
              asof={visibleFlow.asof}
            />

            <ul className="flow-legend">
              <li>
                <i style={{ background: "#2f5d50" }} />
                실데이터
              </li>
              {visibleFlow.forecasts.map((f) => (
                <li key={f.id}>
                  <i style={{ background: f.color }} />
                  {f.label} · {f.outlook === "up" ? "Up" : "Down"} {f.change_pct > 0 ? "+" : ""}
                  {f.change_pct}% · {f.regime}
                </li>
              ))}
            </ul>
          </section>

          <p className="disclaimer">{visibleFlow.disclaimer}</p>
        </>
      )}

      {loading && !visibleFlow && (
        <p className="status">선택 섹터 불러오는 중… (캐시되면 바로 전환됩니다)</p>
      )}
    </div>
  );
}
