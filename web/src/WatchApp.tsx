import { useCallback, useEffect, useMemo, useState } from "react";
import { EggChart, type ModelMark } from "./EggChart";
import {
  REGIME_GUIDE,
  TOP_MODELS,
  rimFromProba,
  type ModelId,
  type RegimeCode,
} from "./eggGeometry";
import { fetchWatch, type ReplayFrame, type Snapshot } from "./api";

const MARKETS = [
  { value: "KS11", label: "KOSPI" },
  { value: "^GSPC", label: "S&P 500" },
  { value: "BTC-USD", label: "BTC" },
] as const;

function frameProba(f: ReplayFrame): Record<string, number> {
  return f.probabilities;
}

type Props = { onBack?: () => void };

export default function WatchApp({ onBack }: Props) {
  const [symbol, setSymbol] = useState<string>("KS11");
  const [snaps, setSnaps] = useState<Partial<Record<ModelId, Snapshot>>>({});
  const [replays, setReplays] = useState<Partial<Record<ModelId, ReplayFrame[]>>>({});
  const [cursor, setCursor] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [focus, setFocus] = useState<ModelId>("hmm");

  const load = useCallback(async (sym: string) => {
    setLoading(true);
    setError(null);
    setPlaying(false);
    try {
      const bundle = await fetchWatch(
        sym,
        TOP_MODELS.map((m) => m.id),
        360,
      );
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
      const ranked = TOP_MODELS.map((m) => ({
        id: m.id,
        conf: nextSnaps[m.id]?.confidence ?? 0,
      })).sort((a, b) => b.conf - a.conf);
      if (ranked[0]) setFocus(ranked[0].id);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setError(
        msg.includes("Failed to fetch") || msg.includes("ECONNREFUSED")
          ? "API 서버에 연결할 수 없습니다. 터미널에서 `kostolany serve`를 실행하세요."
          : msg,
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load(symbol);
  }, [symbol, load]);

  const focusFrames = replays[focus] ?? [];
  const focusMeta = TOP_MODELS.find((m) => m.id === focus)!;

  useEffect(() => {
    if (!playing || focusFrames.length === 0) return;
    const id = window.setInterval(() => {
      setCursor((c) => {
        if (c >= focusFrames.length - 1) {
          setPlaying(false);
          return c;
        }
        return c + 1;
      });
    }, 120);
    return () => window.clearInterval(id);
  }, [playing, focusFrames.length]);

  const modelMarks: ModelMark[] = useMemo(() => {
    return TOP_MODELS.map((m) => {
      const live = snaps[m.id];
      const frames = replays[m.id] ?? [];
      const frame = frames[Math.min(cursor, Math.max(0, frames.length - 1))];
      const probs = frame ? frameProba(frame) : live?.probabilities ?? {};
      const conf = frame?.confidence ?? live?.confidence ?? 0.4;
      return {
        id: m.id,
        label: m.short,
        color: m.color,
        probabilities: probs,
        confidence: conf,
      };
    }).filter((m) => Object.keys(m.probabilities).length > 0);
  }, [snaps, replays, cursor]);

  const focusSnap = snaps[focus];
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
      {onBack && (
        <nav className="topnav">
          <button type="button" className="nav-quiet nav-btn" onClick={onBack}>
            ← 소개
          </button>
        </nav>
      )}

      <section className="hero hero-watch">
        <div className="hero-copy fade-up">
          <h1 className="brand">Kostolany Watch</h1>
          <p className="tagline">리듬이 · 눈치왕 · 파도꾼 — 세 AI가 달걀 외곽 어디를 짚는지 봅니다.</p>

          {error && (
            <p className="status">
              오류: {error}
              <button type="button" className="linkish" onClick={() => void load(symbol)}>
                다시 시도
              </button>
            </p>
          )}

          {!error && (
            <div className="regime-brief fade-up">
              <div className="regime-brief-head">
                <strong style={{ color: guide.color }}>{regime}</strong>
                <span className="regime-brief-name">{guide.name}</span>
                {loading ? (
                  <span className="status">AI 회의 중…</span>
                ) : (
                  <span className="status">
                    {(confidence * 100).toFixed(0)}% · {asof}
                    {!atLive ? " · 리플레이" : ""}
                  </span>
                )}
              </div>

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

              {agreement && !loading && (
                <p className="brief-agree">
                  AI 합의:{" "}
                  <strong style={{ color: REGIME_GUIDE[agreement.regime].color }}>{agreement.regime}</strong>
                  {" · "}
                  {agreement.n}/{agreement.total} 일치
                </p>
              )}

              {!loading && evidence.length > 0 && (
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
                disabled={loading}
                onClick={() => setSymbol(m.value)}
              >
                {m.label}
              </button>
            ))}
          </div>

          <EggChart models={modelMarks} focusId={focus} loading={loading} />
        </div>
      </section>

      {focusFrames.length > 0 && (
        <section className="section">
          <h2>과거 달걀 리플레이</h2>
          <p className="lead">
            {focusMeta.label} 기준으로 경로를 재생합니다. 달걀 위 세 점은 같은 시점의 리듬이·눈치왕·파도꾼
            위치입니다.
          </p>
          <div className="replay-controls">
            <button type="button" onClick={() => setPlaying((p) => !p)}>
              {playing ? "정지" : "재생"}
            </button>
            <button
              type="button"
              onClick={() => {
                setPlaying(false);
                const lens = TOP_MODELS.map((m) => (replays[m.id] ?? []).length);
                setCursor(Math.max(0, Math.min(...lens) - 1));
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
                setPlaying(false);
                setCursor(Number(e.target.value));
              }}
              aria-label="리플레이 시점"
            />
            <span className="status">
              {focusFrames[Math.min(cursor, focusFrames.length - 1)]?.date}
            </span>
          </div>
        </section>
      )}

      {focusSnap && !error && (
        <>
          <section className="section">
            <h2>국면 한눈에</h2>
            <p className="lead">여섯 국면의 특징과 소신파 관점의 권고 행동입니다.</p>
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
            <p className="lead">코스톨라니 두 축(거래량·참여)과 두 동인(돈·심리), 그리고 가격 위치.</p>
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
