import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { EggChart, type ModelMark } from "./EggChart";
import {
  REGIME_GUIDE,
  TOP_MODELS,
  anglesAlongCycle,
  cycleSkipNote,
  needsCycleTransit,
  rimFromProba,
  type ModelId,
  type RegimeCode,
} from "./eggGeometry";
import {
  fetchWatch,
  peekWatch,
  startWatchWarmup,
  type ReplayFrame,
  type Snapshot,
  type WatchBundle,
} from "./api";

const MARKETS = [
  { value: "KS11", label: "KOSPI" },
  { value: "^GSPC", label: "S&P 500" },
  { value: "BTC-USD", label: "BTC" },
] as const;

const MODEL_IDS = TOP_MODELS.map((m) => m.id);

function frameProba(f: ReplayFrame): Record<string, number> {
  return f.probabilities;
}

function formatKst(iso?: string | null): string {
  if (!iso) return "";
  try {
    return new Date(iso).toLocaleString("ko-KR", { timeZone: "Asia/Seoul" });
  } catch {
    return iso;
  }
}

type Props = { onBack?: () => void; onNews?: () => void; onFlows?: () => void };

export default function WatchApp({ onBack, onNews, onFlows }: Props) {
  const [symbol, setSymbol] = useState<string>("KS11");
  const [snaps, setSnaps] = useState<Partial<Record<ModelId, Snapshot>>>({});
  const [replays, setReplays] = useState<Partial<Record<ModelId, ReplayFrame[]>>>({});
  const [cursor, setCursor] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [transit, setTransit] = useState<{
    angles: number[];
    step: number;
    note: string | null;
    targetCursor: number;
  } | null>(null);
  const [skipNote, setSkipNote] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [focus, setFocus] = useState<ModelId>("hmm");
  const [cacheMeta, setCacheMeta] = useState<Pick<
    WatchBundle,
    "cached" | "stale" | "refreshing" | "cached_at" | "expires_at" | "can_refresh" | "refresh_available_at"
  > | null>(null);
  const loadGen = useRef(0);
  const memRef = useRef<Map<string, WatchBundle>>(new Map());
  const pollRef = useRef<number | null>(null);

  const applyBundle = useCallback((bundle: WatchBundle) => {
    memRef.current.set(bundle.symbol, bundle);
    const nextSnaps: Partial<Record<ModelId, Snapshot>> = {};
    const nextReplays: Partial<Record<ModelId, ReplayFrame[]>> = {};
    let minLen = Infinity;
    for (const a of bundle.analysts) {
      const id = a.id as ModelId;
      nextSnaps[id] = a.snapshot;
      nextReplays[id] = a.replay.frames;
      minLen = Math.min(minLen, a.replay.frames.length);
    }
    setSnaps(nextSnaps);
    setReplays(nextReplays);
    setCursor(Math.max(0, minLen - 1));
    setCacheMeta({
      cached: bundle.cached,
      stale: bundle.stale,
      refreshing: bundle.refreshing,
      cached_at: bundle.cached_at,
      expires_at: bundle.expires_at,
      can_refresh: bundle.can_refresh,
      refresh_available_at: bundle.refresh_available_at,
    });
    const ranked = TOP_MODELS.map((m) => ({
      id: m.id,
      conf: nextSnaps[m.id]?.confidence ?? 0,
    })).sort((a, b) => b.conf - a.conf);
    if (ranked[0]) setFocus(ranked[0].id);
  }, []);

  const stopPoll = useCallback(() => {
    if (pollRef.current != null) {
      window.clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const pollFresh = useCallback(
    (sym: string, prevCachedAt?: string | null) => {
      stopPoll();
      setRefreshing(true);
      let ticks = 0;
      pollRef.current = window.setInterval(() => {
        ticks += 1;
        void peekWatch(sym, MODEL_IDS, 360).then((next) => {
          if (next.status !== "hit") return;
          const data = next.data;
          const newer = Boolean(data.cached_at && data.cached_at !== prevCachedAt);
          const done = !data.refreshing && (!data.stale || newer);
          if (newer || done) applyBundle(data);
          if (done || ticks >= 48) {
            setRefreshing(false);
            stopPoll();
          }
        });
      }, 2500);
    },
    [applyBundle, stopPoll],
  );

  const load = useCallback(
    async (sym: string, refresh = false) => {
      const gen = ++loadGen.current;
      const mem = memRef.current.get(sym);
      if (mem) {
        applyBundle(mem);
        setLoading(false);
      } else if (!refresh) {
        setLoading(true);
      }
      setError(null);
      setPlaying(false);

      try {
        if (!refresh) {
          const peeked = await peekWatch(sym, MODEL_IDS, 360);
          if (gen !== loadGen.current) return;
          if (peeked.status === "hit") {
            applyBundle(peeked.data);
            setLoading(false);
            if (peeked.data.refreshing || peeked.data.stale) {
              pollFresh(sym, peeked.data.cached_at);
            } else {
              setRefreshing(false);
              stopPoll();
            }
            // Soft refresh in background — do not block / stampede the API
            void fetchWatch(sym, MODEL_IDS, 360, false)
              .then((data) => {
                if (gen !== loadGen.current) return;
                applyBundle(data);
                if (data.refreshing || data.stale) pollFresh(sym, data.cached_at);
              })
              .catch(() => undefined);
            return;
          }

          // busy or miss: poll peeks; only kick warmup on confirmed miss
          if (peeked.status === "miss") {
            void startWatchWarmup(false).catch(() => undefined);
          }
          for (let i = 0; i < 36; i++) {
            await new Promise((r) => setTimeout(r, peeked.status === "busy" ? 4000 : 2500));
            if (gen !== loadGen.current) return;
            const again = await peekWatch(sym, MODEL_IDS, 360);
            if (again.status === "hit") {
              applyBundle(again.data);
              setLoading(false);
              if (again.data.refreshing || again.data.stale) pollFresh(sym, again.data.cached_at);
              else {
                setRefreshing(false);
                stopPoll();
              }
              return;
            }
            if (again.status === "miss" && i === 2) {
              void startWatchWarmup(false).catch(() => undefined);
            }
          }
        }

        const data = await fetchWatch(sym, MODEL_IDS, 360, refresh);
        if (gen !== loadGen.current) return;
        applyBundle(data);
        setLoading(false);

        if (data.refreshing || data.stale || refresh) {
          pollFresh(sym, data.cached_at);
        } else {
          setRefreshing(false);
          stopPoll();
        }
      } catch (e) {
        if (gen !== loadGen.current) return;
        if (!memRef.current.get(sym)) {
          const msg = e instanceof Error ? e.message : String(e);
          setError(
            msg.includes("Failed to fetch") || msg.includes("ECONNREFUSED")
              ? "API 서버에 연결할 수 없습니다. 터미널에서 `kostolany serve`를 실행하세요."
              : msg,
          );
        }
        setLoading(false);
        setRefreshing(false);
      }
    },
    [applyBundle, pollFresh, stopPoll],
  );

  useEffect(() => {
    void startWatchWarmup(false).catch(() => undefined);
    for (const m of MARKETS) {
      void peekWatch(m.value, MODEL_IDS, 360).then((p) => {
        if (p.status === "hit") memRef.current.set(m.value, p.data);
      });
    }
  }, []);

  useEffect(() => {
    void load(symbol, false);
    return () => stopPoll();
  }, [symbol, load, stopPoll]);

  const focusFrames = replays[focus] ?? [];
  const focusMeta = TOP_MODELS.find((m) => m.id === focus)!;
  const hasAny = Object.keys(snaps).length > 0;
  const stillLoading = loading && !hasAny;
  const bgBusy = refreshing || Boolean(cacheMeta?.refreshing);

  useEffect(() => {
    setTransit(null);
    setSkipNote(null);
  }, [focus, symbol]);

  const stopReplay = useCallback(() => {
    setPlaying(false);
    setTransit(null);
  }, []);

  const focusFramesRef = useRef(focusFrames);
  focusFramesRef.current = focusFrames;
  const cursorRef = useRef(cursor);
  cursorRef.current = cursor;
  const transitRef = useRef(transit);
  transitRef.current = transit;

  useEffect(() => {
    if (!playing) return;
    const id = window.setInterval(() => {
      const frames = focusFramesRef.current;
      if (frames.length === 0) {
        setPlaying(false);
        return;
      }

      const tr = transitRef.current;
      if (tr) {
        const nextStep = tr.step + 1;
        if (nextStep >= tr.angles.length) {
          setCursor(tr.targetCursor);
          setTransit(null);
        } else {
          setTransit({ ...tr, step: nextStep });
        }
        return;
      }

      const c = cursorRef.current;
      if (c >= frames.length - 1) {
        setPlaying(false);
        return;
      }
      const cur = frames[c];
      const nxt = frames[c + 1];
      if (!cur || !nxt) {
        setCursor(c + 1);
        return;
      }

      const fromRim = rimFromProba(frameProba(cur));
      const toRim = rimFromProba(frameProba(nxt));
      const fromR = (cur.regime as RegimeCode) || fromRim.regime;
      const toR = (nxt.regime as RegimeCode) || toRim.regime;

      if (needsCycleTransit(fromR, toR, fromRim.angle, toRim.angle)) {
        const built = anglesAlongCycle(fromR, toR, 5);
        const note = cycleSkipNote(fromR, toR);
        setSkipNote(note);
        setTransit({
          angles: built.angles,
          step: 0,
          note,
          targetCursor: c + 1,
        });
      } else {
        setSkipNote(null);
        setCursor(c + 1);
      }
    }, 55);
    return () => window.clearInterval(id);
  }, [playing]);

  const modelMarks: ModelMark[] = useMemo(() => {
    return TOP_MODELS.map((m) => {
      const live = snaps[m.id];
      const frames = replays[m.id] ?? [];
      const frame = frames[Math.min(cursor, Math.max(0, frames.length - 1))];
      const probs = frame ? frameProba(frame) : live?.probabilities ?? {};
      const conf = frame?.confidence ?? live?.confidence ?? 0.4;
      const mark: ModelMark = {
        id: m.id,
        label: m.short,
        color: m.color,
        probabilities: probs,
        confidence: conf,
      };
      if (transit && m.id === focus && transit.step < transit.angles.length) {
        mark.angleOverride = transit.angles[transit.step];
      }
      return mark;
    }).filter((m) => Object.keys(m.probabilities).length > 0);
  }, [snaps, replays, cursor, transit, focus]);

  const focusSnap = snaps[focus] ?? Object.values(snaps)[0];
  const focusFrame = focusFrames[Math.min(cursor, Math.max(0, focusFrames.length - 1))];
  const focusProbs = focusFrame?.probabilities ?? focusSnap?.probabilities ?? {};
  const rim = rimFromProba(focusProbs);
  const regime = (focusFrame?.regime ?? focusSnap?.regime ?? rim.regime) as RegimeCode;
  const guide = REGIME_GUIDE[regime] ?? REGIME_GUIDE.A2;
  const confidence = focusFrame?.confidence ?? focusSnap?.confidence ?? 0;
  const asof = focusFrame?.date ?? focusSnap?.asof ?? "";
  const atLive = focusSnap && focusFrame && focusFrame.date === focusSnap.asof;

  const agreement = useMemo(() => {
    const votes = modelMarks.map((m) => rimFromProba(m.probabilities).regime);
    if (votes.length < 2) return null;
    const counts: Record<string, number> = {};
    for (const v of votes) counts[v] = (counts[v] ?? 0) + 1;
    const top = Object.entries(counts).sort((a, b) => b[1] - a[1])[0];
    return top ? { regime: top[0] as RegimeCode, n: top[1], total: votes.length } : null;
  }, [modelMarks]);

  const evidence = useMemo(() => {
    if (focusSnap?.evidence?.length) return focusSnap.evidence;
    const g = focusFrame?.gauges ?? focusSnap?.gauges;
    if (!g) return [];
    const level = (v: number) => (v < 0.35 ? "낮음" : v < 0.65 ? "보통" : "높음");
    return (
      [
        ["volume", "거래량", "회전·거래대금 강도"],
        ["participation", "참여", "시장 폭·참여 추세"],
        ["money", "돈(유동성)", "금리·신용·유동성 프록시"],
        ["sentiment", "심리", "위험선호·변동성 심리"],
      ] as const
    ).map(([key, label, detail]) => ({
      key,
      label,
      value: g[key] ?? 0.5,
      level: level(g[key] ?? 0.5),
      detail,
    }));
  }, [focusSnap, focusFrame]);

  return (
    <div className="page">
      {(onBack || onNews || onFlows) && (
        <nav className="topnav desk-nav">
          {onBack && (
            <button type="button" className="nav-quiet nav-btn" onClick={onBack}>
              ← 소개
            </button>
          )}
          <div className="desk-tabs" role="tablist" aria-label="화면">
            <button type="button" className="desk-tab is-active" aria-current="page">
              국면
            </button>
            <button type="button" className="desk-tab" onClick={onNews}>
              뉴스
            </button>
            <button type="button" className="desk-tab" onClick={onFlows}>
              흐름
            </button>
          </div>
        </nav>
      )}

      <section className="hero hero-watch">
        <div className="hero-copy fade-up">
          <h1 className="brand">Kostolany Watch</h1>

          {error && (
            <p className="status">
              오류: {error}
              <button type="button" className="linkish" onClick={() => void load(symbol, false)}>
                다시 시도
              </button>
            </p>
          )}

          {!error && (
            <div className="cache-bar">
              <span className="status">
                {stillLoading
                  ? "준비 중…"
                  : bgBusy
                    ? `백그라운드 갱신 · ${formatKst(cacheMeta?.cached_at)}`
                    : cacheMeta?.stale
                      ? `캐시(갱신 대기) · ${formatKst(cacheMeta?.cached_at)}`
                      : cacheMeta?.cached
                        ? `캐시 · ${formatKst(cacheMeta.cached_at)}`
                        : `최신 · ${formatKst(cacheMeta?.cached_at)}`}
              </span>
              <button
                type="button"
                className="btn-refresh"
                disabled={stillLoading || cacheMeta?.can_refresh === false}
                title={
                  cacheMeta?.can_refresh === false
                    ? `다음 리프레시: ${formatKst(cacheMeta.refresh_available_at)}`
                    : "백그라운드 재계산 (1시간에 한 번)"
                }
                onClick={() => void load(symbol, true)}
              >
                {bgBusy ? "갱신 중…" : "새로고침"}
              </button>
            </div>
          )}

          {!error && (hasAny || stillLoading) && (
            <div className="regime-brief fade-up">
              <div className="regime-brief-head">
                {hasAny ? (
                  <>
                    <strong style={{ color: guide.color }}>{regime}</strong>
                    <span className="regime-brief-name">{guide.name}</span>
                    <span className="status">
                      {(confidence * 100).toFixed(0)}% · {asof}
                      {!atLive && focusFrame ? " · 리플레이" : ""}
                      {bgBusy ? " · 갱신 중" : ""}
                    </span>
                  </>
                ) : (
                  <span className="status">첫 AI 응답을 기다리는 중…</span>
                )}
              </div>

              {hasAny && (
                <>
                  <p className="analyst-focus">
                    <span className="analyst-focus-name" style={{ color: focusMeta.color }}>
                      {focusMeta.label}
                    </span>
                    <span className="analyst-focus-trait">{focusMeta.trait}</span>
                    <span className="analyst-focus-blurb">{focusMeta.blurb}</span>
                  </p>

                  <dl className="brief-grid">
                    <div>
                      <dt>특징</dt>
                      <dd>{guide.trait}</dd>
                    </div>
                    <div>
                      <dt>거래량</dt>
                      <dd>{guide.volume}</dd>
                    </div>
                    <div>
                      <dt>참여자</dt>
                      <dd>{guide.crowd}</dd>
                    </div>
                    <div>
                      <dt>권고 행동</dt>
                      <dd className="brief-action">{guide.action}</dd>
                    </div>
                  </dl>
                </>
              )}

              {agreement && (
                <p className="brief-agree">
                  AI 합의:{" "}
                  <strong style={{ color: REGIME_GUIDE[agreement.regime].color }}>{agreement.regime}</strong>
                  {" · "}
                  {agreement.n}/{agreement.total} 일치
                  {bgBusy ? " (백그라운드 갱신)" : ""}
                </p>
              )}

              {evidence.length > 0 && (
                <div className="evidence-panel">
                  <h3>근거 지표</h3>
                  <ul>
                    {evidence.map((e) => (
                      <li key={e.key}>
                        <div className="evidence-top">
                          <span className="evidence-label">{e.label}</span>
                          <span className="evidence-level">{e.level}</span>
                          {typeof e.value === "number" && e.key !== "position" && (
                            <span className="evidence-val">{Math.round(e.value * 100)}</span>
                          )}
                        </div>
                        {e.key !== "position" && (
                          <div className="evidence-meter">
                            <i style={{ width: `${Math.min(100, Math.max(0, e.value * 100))}%` }} />
                          </div>
                        )}
                        <p className="evidence-detail">{e.detail}</p>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}

          <div className="model-legend">
            {TOP_MODELS.map((m) => {
              const mark = modelMarks.find((x) => x.id === m.id);
              const r = mark ? rimFromProba(mark.probabilities).regime : "—";
              const active = focus === m.id;
              return (
                <button
                  key={m.id}
                  type="button"
                  className={`model-chip${active ? " is-active" : ""}`}
                  style={{ ["--chip" as string]: m.color }}
                  onClick={() => setFocus(m.id)}
                  disabled={!mark}
                  title={m.blurb}
                >
                  <i style={{ background: m.color }} />
                  <span className="model-chip-text">
                    <span className="model-chip-name">{m.label}</span>
                    <span className="model-chip-trait">{m.trait}</span>
                  </span>
                  <span className="model-chip-reg">{r}</span>
                </button>
              );
            })}
          </div>
        </div>

        <div className="egg-stage fade-up">
          <div className="market-tabs" role="tablist" aria-label="시장">
            {MARKETS.map((m) => (
              <button
                key={m.value}
                type="button"
                role="tab"
                aria-selected={symbol === m.value}
                className={`market-tab${symbol === m.value ? " is-active" : ""}`}
                onClick={() => setSymbol(m.value)}
              >
                {m.label}
              </button>
            ))}
          </div>

          <EggChart
            models={modelMarks}
            focusId={focus}
            loading={!hasAny && stillLoading}
            pendingLabel={bgBusy ? "백그라운드 갱신 중" : null}
          />
        </div>
      </section>

      {focusFrames.length > 0 && (
        <section className="section">
          <h2>과거 달걀 리플레이</h2>
          <div className="replay-controls">
            <button
              type="button"
              onClick={() => {
                if (playing) stopReplay();
                else setPlaying(true);
              }}
            >
              {playing ? "정지" : "재생"}
            </button>
            <button
              type="button"
              onClick={() => {
                stopReplay();
                const lens = TOP_MODELS.map((m) => (replays[m.id] ?? []).length).filter((n) => n > 0);
                setCursor(Math.max(0, Math.min(...lens) - 1));
                setSkipNote(null);
              }}
            >
              오늘
            </button>
            <input
              type="range"
              min={0}
              max={Math.max(0, focusFrames.length - 1)}
              value={Math.min(cursor, Math.max(0, focusFrames.length - 1))}
              onChange={(e) => {
                stopReplay();
                setSkipNote(null);
                setCursor(Number(e.target.value));
              }}
              aria-label="리플레이 시점"
            />
            <span className="status">
              {focusFrames[Math.min(cursor, focusFrames.length - 1)]?.date}
              {transit ? " · 동선 통과 중" : ""}
            </span>
          </div>
          {skipNote && <p className="replay-skip-note">{skipNote}</p>}
        </section>
      )}

      {focusSnap && !error && (
        <>
          <section className="section">
            <h2>국면 한눈에</h2>
            <div className="guide-table">
              {(Object.keys(REGIME_GUIDE) as RegimeCode[]).map((code) => {
                const g = REGIME_GUIDE[code];
                const p = focusProbs[code] ?? 0;
                const on = code === regime;
                return (
                  <div key={code} className={`guide-row${on ? " is-on" : ""}`}>
                    <div className="guide-code" style={{ color: g.color }}>
                      {code}
                    </div>
                    <div className="guide-main">
                      <div className="guide-title">
                        {g.name}
                        <span className="guide-pct">{(p * 100).toFixed(0)}%</span>
                      </div>
                      <p>{g.trait}</p>
                      <p className="guide-meta">
                        {g.volume} · {g.crowd}
                      </p>
                    </div>
                    <div className="guide-action">{g.action}</div>
                  </div>
                );
              })}
            </div>
          </section>

          <section className="section">
            <h2>판단 근거</h2>
            <div className="evidence-panel evidence-panel-wide">
              <ul>
                {evidence.map((e) => (
                  <li key={`wide-${e.key}`}>
                    <div className="evidence-top">
                      <span className="evidence-label">{e.label}</span>
                      <span className="evidence-level">{e.level}</span>
                      {e.key !== "position" && (
                        <span className="evidence-val">{Math.round(e.value * 100)}</span>
                      )}
                    </div>
                    {e.key !== "position" && (
                      <div className="evidence-meter">
                        <i style={{ width: `${Math.min(100, Math.max(0, e.value * 100))}%` }} />
                      </div>
                    )}
                    <p className="evidence-detail">{e.detail}</p>
                  </li>
                ))}
              </ul>
            </div>
          </section>

          <p className="disclaimer">{focusSnap.disclaimer}</p>
        </>
      )}
    </div>
  );
}
