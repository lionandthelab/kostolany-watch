import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { EggChart, type ModelMark } from "./EggChart";
import {
  REGIME_GUIDE,
  TOP_MODELS,
  pctFloor,
  rimFromProba,
  type ModelId,
  type RegimeCode,
} from "./eggGeometry";
import {
  ensureWatchMarket,
  fetchWatch,
  peekWatch,
  startWatchWarmup,
  type Snapshot,
  type RegimeCalibration,
  type WatchBundle,
} from "./api";
import { useLocale, useT } from "./i18n";
import { trackEvent } from "./analytics";

/** Regime markets: US equities + crypto (no Korea). */
const MARKETS = [
  { value: "^GSPC", labelKey: "marketUs" as const },
  { value: "BTC-USD", labelKey: "marketCrypto" as const },
] as const;

const MODEL_IDS = TOP_MODELS.map((m) => m.id);

function ExplainModal({
  title,
  open,
  onClose,
  closeLabel,
  children,
}: {
  title: string;
  open: boolean;
  onClose: () => void;
  closeLabel: string;
  children: ReactNode;
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = prev;
    };
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="modal-backdrop" role="presentation" onClick={onClose}>
      <div
        className="modal-sheet"
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-head">
          <h3>{title}</h3>
          <button type="button" className="modal-close" onClick={onClose} aria-label={closeLabel}>
            ×
          </button>
        </div>
        <div className="modal-body">{children}</div>
      </div>
    </div>
  );
}

function fill(tpl: string, slots: Record<string, string>): string {
  return tpl.replace(/\{(\w+)\}/g, (_, k) => slots[k] ?? `{${k}}`);
}

function formatCalibrationNote(cal: RegimeCalibration, tpl: string): string {
  const vals = Object.values(cal.measured);
  if (!vals.length) return cal.note_ko;
  const exactLo = Math.round(Math.min(...vals.map((v) => v.exact6)) * 100);
  const exactHi = Math.round(Math.max(...vals.map((v) => v.exact6)) * 100);
  const exact = exactLo === exactHi ? `${exactLo}%` : `${exactLo}~${exactHi}%`;
  const eceLo = Math.min(...vals.map((v) => v.ece)).toFixed(2);
  const eceHi = Math.max(...vals.map((v) => v.ece)).toFixed(2);
  const sideHit = cal.momo_floor?.vote_side ?? cal.momo_floor?.side_median ?? 0;
  return fill(tpl, {
    window: cal.window,
    n: cal.n_oos_bars.toLocaleString(),
    exact,
    chance: `${Math.round(cal.exact6_chance * 100)}`,
    eceLo,
    eceHi,
    sidePct: `${Math.round(sideHit * 100)}`,
  });
}

export default function WatchApp() {
  const t = useT();
  const { formatDate } = useLocale();
  const [symbol, setSymbol] = useState<string>("^GSPC");
  const [snaps, setSnaps] = useState<Partial<Record<ModelId, Snapshot>>>({});
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [focus, setFocus] = useState<ModelId>("momo");
  const [cacheMeta, setCacheMeta] = useState<Pick<
    WatchBundle,
    "cached" | "stale" | "refreshing" | "cached_at" | "expires_at" | "can_refresh" | "refresh_available_at"
  > | null>(null);
  const [calibration, setCalibration] = useState<RegimeCalibration | null>(null);
  const [modal, setModal] = useState<"regime" | "analysts" | null>(null);
  const loadGen = useRef(0);
  const memRef = useRef<Map<string, WatchBundle>>(new Map());
  const pollRef = useRef<number | null>(null);

  const applyBundle = useCallback((bundle: WatchBundle) => {
    memRef.current.set(bundle.symbol, bundle);
    const nextSnaps: Partial<Record<ModelId, Snapshot>> = {};
    for (const a of bundle.analysts) {
      nextSnaps[a.id as ModelId] = a.snapshot;
    }
    setSnaps(nextSnaps);
    setCacheMeta({
      cached: bundle.cached,
      stale: bundle.stale,
      refreshing: bundle.refreshing,
      cached_at: bundle.cached_at,
      expires_at: bundle.expires_at,
      can_refresh: bundle.can_refresh,
      refresh_available_at: bundle.refresh_available_at,
    });
    setCalibration(bundle.calibration ?? null);
    // Fixed default focus (S4): the measured-best head leads. Never pick the
    // focus by uncalibrated confidence — that was auditing finding P4.2.
    setFocus((cur) => (nextSnaps[cur] ? cur : nextSnaps.momo ? "momo" : (Object.keys(nextSnaps)[0] as ModelId)));
  }, []);

  const stopPoll = useCallback(() => {
    if (pollRef.current != null) {
      window.clearTimeout(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const pollFresh = useCallback(
    (sym: string, prevCachedAt?: string | null) => {
      stopPoll();
      setRefreshing(true);
      let ticks = 0;
      let delay = 4000;
      const tick = () => {
        ticks += 1;
        void peekWatch(sym, MODEL_IDS, 360).then((next) => {
          if (next.status === "busy") {
            delay = Math.min(30000, Math.round(delay * 1.5));
            if (ticks < 30) pollRef.current = window.setTimeout(tick, delay);
            else {
              setRefreshing(false);
              stopPoll();
            }
            return;
          }
          if (next.status === "hit") {
            const data = next.data;
            const newer = Boolean(data.cached_at && data.cached_at !== prevCachedAt);
            const done = !data.refreshing && (!data.stale || newer);
            if (newer || done) applyBundle(data);
            if (done || ticks >= 30) {
              setRefreshing(false);
              stopPoll();
              return;
            }
            delay = 4000;
          }
          if (ticks < 30) pollRef.current = window.setTimeout(tick, delay);
          else {
            setRefreshing(false);
            stopPoll();
          }
        });
      };
      pollRef.current = window.setTimeout(tick, delay);
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
            return;
          }

          if (peeked.status === "miss" || peeked.status === "busy") {
            void ensureWatchMarket(sym).catch(() => undefined);
          }
          let delay = peeked.status === "busy" ? 5000 : 2500;
          for (let i = 0; i < 40; i++) {
            await new Promise((r) => setTimeout(r, delay));
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
            if (again.status === "busy") {
              delay = Math.min(20000, Math.round(delay * 1.5));
            } else if (again.status === "miss" && i % 4 === 0) {
              void ensureWatchMarket(sym).catch(() => undefined);
            }
          }
          if (!memRef.current.get(sym)) setError(t.common.loadFailed);
          setLoading(false);
          return;
        }

        const data = await fetchWatch(sym, MODEL_IDS, 360, refresh);
        if (gen !== loadGen.current) return;
        applyBundle(data);
        setLoading(false);
        if (data.refreshing || data.stale || refresh) pollFresh(sym, data.cached_at);
        else {
          setRefreshing(false);
          stopPoll();
        }
      } catch (e) {
        if (gen !== loadGen.current) return;
        if (!memRef.current.get(sym)) {
          const msg = e instanceof Error ? e.message : String(e);
          setError(
            msg.includes("Failed to fetch") || msg.includes("ECONNREFUSED")
              ? t.common.loadFailed
              : msg,
          );
        }
        setLoading(false);
        setRefreshing(false);
      }
    },
    [applyBundle, pollFresh, stopPoll, t.common.loadFailed],
  );

  useEffect(() => {
    void startWatchWarmup(false).catch(() => undefined);
  }, []);

  useEffect(() => {
    void load(symbol, false);
    return () => stopPoll();
  }, [symbol, load, stopPoll]);

  const focusCopy = t.models[focus];
  const hasAny = Object.keys(snaps).length > 0;
  const stillLoading = loading && !hasAny;
  const bgBusy = refreshing || Boolean(cacheMeta?.refreshing);

  const modelMarks: ModelMark[] = useMemo(() => {
    return TOP_MODELS.map((m) => {
      const live = snaps[m.id];
      if (!live) return null;
      return {
        id: m.id,
        label: t.models[m.id].label,
        color: m.color,
        probabilities: live.probabilities ?? {},
        confidence: live.confidence ?? 0.4,
      } as ModelMark;
    }).filter((m): m is ModelMark => Boolean(m && Object.keys(m.probabilities).length > 0));
  }, [snaps, t.models]);

  const focusSnap = snaps[focus] ?? Object.values(snaps)[0];
  const vote = focus === "momo" ? (snaps.momo?.vote ?? null) : null;
  const cview = calibration?.confidence_view ?? null;
  const focusProbs = focusSnap?.probabilities ?? {};
  const rim = rimFromProba(focusProbs);
  const regime = (focusSnap?.regime ?? rim.regime) as RegimeCode;
  const guideColor = (REGIME_GUIDE[regime] ?? REGIME_GUIDE.A2).color;
  const guide = t.regimes[regime] ?? t.regimes.A2;
  const asof = focusSnap?.asof ?? "";

  const agreement = useMemo(() => {
    const votes = modelMarks.map((m) => rimFromProba(m.probabilities).regime);
    if (votes.length < 2) return null;
    const counts: Record<string, number> = {};
    for (const v of votes) counts[v] = (counts[v] ?? 0) + 1;
    const top = Object.entries(counts).sort((a, b) => b[1] - a[1])[0];
    return top ? { regime: top[0] as RegimeCode, n: top[1], total: votes.length } : null;
  }, [modelMarks]);

  const evidence = useMemo(() => {
    const level = (v: number) =>
      v < 0.35 ? t.watch.levelLow : v < 0.65 ? t.watch.levelMid : t.watch.levelHigh;
    const gaugeLabel = (key: string, fallback: string) =>
      key in t.watch.gauges ? t.watch.gauges[key as keyof typeof t.watch.gauges] : fallback;
    if (focusSnap?.evidence?.length) {
      return focusSnap.evidence.map((e) => ({
        ...e,
        label: gaugeLabel(e.key, e.label),
        level: level(e.value),
      }));
    }
    const g = focusSnap?.gauges;
    if (!g) return [];
    return (
      [
        ["volume", t.watch.gauges.volume],
        ["participation", t.watch.gauges.participation],
        ["money", t.watch.gauges.money],
        ["sentiment", t.watch.gauges.sentiment],
      ] as const
    ).map(([key, label]) => ({
      key,
      label,
      value: g[key] ?? 0.5,
      level: level(g[key] ?? 0.5),
      detail: "",
    }));
  }, [focusSnap, t]);

  const alignLine =
    focus === "momo" && vote
      ? vote.tier === "mixed"
        ? fill(t.conviction.badge.mixed, {
            a: String(Math.max(vote.up, vote.down)),
            b: String(Math.min(vote.up, vote.down)),
          })
        : fill(t.conviction.badge[vote.tier], {
            side: t.conviction.sideWord[vote.side],
          })
      : null;

  const directionLine =
    focus === "momo" && vote && cview
      ? vote.tier === "mixed"
        ? fill(t.conviction.directionMixed, {
            p: pctFloor(cview.tiers.mixed.side_hit),
          })
        : fill(t.conviction.directionAligned, {
            tierName: t.conviction.tierName[vote.tier],
            side: t.conviction.sideWord[vote.side],
            p: pctFloor(cview.tiers[vote.tier].side_hit),
          })
      : null;

  return (
    <div className="desk-panel watch-slim">
      <section className="hero hero-watch">
        <div className="hero-copy fade-up">
          <div className="watch-head desk-hero-row">
            <h1 className="desk-title">Kostolany Watch</h1>
            <div className="cache-bar desk-hero-meta">
              <span className="status">
                {stillLoading ? t.common.loading : formatDate(cacheMeta?.cached_at) || asof}
              </span>
              <button
                type="button"
                className="btn-refresh"
                disabled={stillLoading || cacheMeta?.can_refresh === false}
                onClick={() => void load(symbol, true)}
              >
                {bgBusy ? t.common.refreshing : t.common.refresh}
              </button>
            </div>
          </div>

          {error && (
            <p className="status">
              {t.common.error}: {error}{" "}
              <button type="button" className="linkish" onClick={() => void load(symbol, false)}>
                {t.common.retry}
              </button>
            </p>
          )}

          {hasAny && (
            <div className="regime-compact fade-up">
              <div className="regime-compact-head">
                <strong style={{ color: guideColor }}>{regime}</strong>
                <span className="regime-brief-name">{guide.name}</span>
              </div>
              <p className="regime-trait">{guide.trait}</p>

              {focus === "momo" && vote && (
                <div className="conviction-card conviction-card--slim">
                  <div className="align-badge">
                    <span className="vote-dots" aria-hidden="true">
                      {vote.rules.map((r) => (
                        <i key={r.id} className={`vote-dot is-${r.vote}`} />
                      ))}
                    </span>
                    <span className="align-badge-text">{alignLine}</span>
                  </div>
                  {directionLine && <p className="conviction-direction">{directionLine}</p>}
                  {cview && (
                    <p className="conviction-zone">
                      {fill(t.conviction.zoneLine, {
                        regime,
                        p: pctFloor(cview.menu.zone1_hit),
                      })}
                    </p>
                  )}
                  {!cview && <p className="status">{t.conviction.unmeasured}</p>}

                  <details className="watch-details">
                    <summary>{t.conviction.detailTitle}</summary>
                    <div className="watch-details-body">
                      {cview && (
                        <>
                          <ul className="claims-ladder">
                            <li>
                              {fill(t.conviction.ladderDirection, {
                                p: pctFloor(cview.menu.side_hit),
                              })}
                            </li>
                            <li>
                              {fill(t.conviction.ladderZone, {
                                p1: pctFloor(cview.menu.zone1_hit),
                                p2: pctFloor(cview.menu.zone2_hit),
                              })}
                            </li>
                            <li>
                              {fill(t.conviction.ladderExact, {
                                p: pctFloor(cview.menu.exact_hit),
                                ceiling: pctFloor(cview.menu.exact_ceiling),
                              })}
                            </li>
                          </ul>
                          <p className="status">{t.conviction.ladderExactWhy}</p>
                          <table className="tier-table">
                            <thead>
                              <tr>
                                <th>{t.conviction.tierTableCols.tier}</th>
                                <th>{t.conviction.tierTableCols.side}</th>
                                <th>{t.conviction.tierTableCols.share}</th>
                              </tr>
                            </thead>
                            <tbody>
                              {(["unanimous", "strong", "lean", "mixed"] as const).map((k) => (
                                <tr key={k} className={vote.tier === k ? "is-current" : undefined}>
                                  <td>{t.conviction.tierTableRows[k]}</td>
                                  <td>{pctFloor(cview.tiers[k].side_hit)}</td>
                                  <td>
                                    {fill(t.conviction.tierTableShare, {
                                      p: pctFloor(cview.tiers[k].share),
                                    })}
                                  </td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </>
                      )}
                      <ul className="rule-ledger">
                        {vote.rules.map((r) => (
                          <li key={r.id} className={`is-${r.vote}`}>
                            <span>
                              {t.conviction.ledgerRules[
                                r.id as keyof typeof t.conviction.ledgerRules
                              ]}
                            </span>
                            <em>{t.conviction.sideWord[r.vote]}</em>
                          </li>
                        ))}
                      </ul>
                      {cview && (
                        <ul className="method-lines">
                          {t.conviction.methodLines.map((line, i) => (
                            <li key={i}>
                              {fill(line, {
                                n: cview.n_bars.toLocaleString(),
                                legs: String(cview.n_legs),
                                source: cview.source,
                              })}
                            </li>
                          ))}
                        </ul>
                      )}
                      {agreement && (
                        <p className="brief-agree">
                          {fill(t.conviction.aiRefTitle, {
                            code: agreement.regime,
                            k: String(agreement.n),
                            total: String(agreement.total),
                          })}
                        </p>
                      )}
                      <div className="explain-row">
                        <button
                          type="button"
                          className="btn-ghost btn-sm"
                          onClick={() => {
                            trackEvent("open_explain", { kind: "regime", symbol });
                            setModal("regime");
                          }}
                        >
                          {t.watch.explainRegime}
                        </button>
                        <button
                          type="button"
                          className="btn-ghost btn-sm"
                          onClick={() => {
                            trackEvent("open_explain", { kind: "analysts", symbol });
                            setModal("analysts");
                          }}
                        >
                          {t.watch.explainAi}
                        </button>
                      </div>
                    </div>
                  </details>
                </div>
              )}

              {evidence.length > 0 && (
                <ul className="gauge-strip">
                  {evidence.map((e) => (
                    <li key={e.key} title={e.detail || undefined}>
                      <span className="gauge-label">{e.label}</span>
                      <div className="evidence-meter">
                        <i style={{ width: `${Math.min(100, Math.max(0, e.value * 100))}%` }} />
                      </div>
                      <em>{e.level}</em>
                    </li>
                  ))}
                </ul>
              )}

              {!(focus === "momo" && vote) && (
                <details className="watch-details">
                  <summary>{t.conviction.detailTitle}</summary>
                  <div className="watch-details-body">
                    {agreement && (
                      <p className="brief-agree">
                        {fill(t.conviction.aiRefTitle, {
                          code: agreement.regime,
                          k: String(agreement.n),
                          total: String(agreement.total),
                        })}
                      </p>
                    )}
                    <div className="explain-row">
                      <button
                        type="button"
                        className="btn-ghost btn-sm"
                        onClick={() => {
                          trackEvent("open_explain", { kind: "regime", symbol });
                          setModal("regime");
                        }}
                      >
                        {t.watch.explainRegime}
                      </button>
                      <button
                        type="button"
                        className="btn-ghost btn-sm"
                        onClick={() => {
                          trackEvent("open_explain", { kind: "analysts", symbol });
                          setModal("analysts");
                        }}
                      >
                        {t.watch.explainAi}
                      </button>
                    </div>
                  </div>
                </details>
              )}
            </div>
          )}

          {focusSnap && <p className="disclaimer disclaimer--compact">{t.common.disclaimer}</p>}
        </div>

        <div className="egg-stage fade-up">
          <div className="egg-stage-toolbar">
            <div className="market-tabs" role="tablist" aria-label={t.watch.markets}>
              {MARKETS.map((m) => (
                <button
                  key={m.value}
                  type="button"
                  role="tab"
                  aria-selected={symbol === m.value}
                  className={`market-tab${symbol === m.value ? " is-active" : ""}`}
                  onClick={() => {
                    setSymbol(m.value);
                    trackEvent("select_market", { symbol: m.value });
                  }}
                >
                  {t.watch[m.labelKey]}
                </button>
              ))}
            </div>
            {focus === "momo" && vote && (
              <button
                type="button"
                className="egg-info"
                title={fill(t.conviction.eggLegend, {
                  sideSectors: vote.side === "up" ? "A1·A2·A3" : "B1·B2·B3",
                })}
                aria-label={fill(t.conviction.eggLegend, {
                  sideSectors: vote.side === "up" ? "A1·A2·A3" : "B1·B2·B3",
                })}
              >
                i
              </button>
            )}
          </div>
          <EggChart
            models={modelMarks}
            sideBand={vote ? { side: vote.side, tier: vote.tier } : null}
            zoneCenter={focus === "momo" ? (regime as RegimeCode) : null}
            focusId={focus}
            onFocus={(id) => {
              setFocus(id);
              trackEvent("focus_analyst", { analyst: id, symbol });
            }}
            loading={!hasAny && stillLoading}
            pendingLabel={null}
          />
        </div>
      </section>

      <ExplainModal
        title={t.watch.regimeModal}
        open={modal === "regime"}
        onClose={() => setModal(null)}
        closeLabel={t.common.close}
      >
        <div className="modal-lead">
          <strong style={{ color: guideColor }}>
            {regime} {guide.name}
          </strong>
          <p>{guide.trait}</p>
          <p className="modal-meta">
            {t.watch.volume} · {guide.volume}
            <br />
            {t.watch.crowd} · {guide.crowd}
          </p>
          <p className="brief-action">
            {t.watch.actionEdu}: {guide.action}
          </p>
        </div>
        <div className="guide-table modal-guide">
          {(Object.keys(REGIME_GUIDE) as RegimeCode[]).map((code) => {
            const g = t.regimes[code];
            const color = REGIME_GUIDE[code].color;
            const p = focusProbs[code] ?? 0;
            return (
              <div key={code} className={`guide-row${code === regime ? " is-on" : ""}`}>
                <div className="guide-code" style={{ color }}>
                  {code}
                </div>
                <div className="guide-main">
                  <div className="guide-title">
                    {g.name}
                    <span className="guide-pct">{(p * 100).toFixed(0)}%</span>
                  </div>
                  <p>{g.trait}</p>
                </div>
              </div>
            );
          })}
        </div>
        {calibration && (
          <p className="disclaimer">
            {t.watch.measuredHit}{" "}
            {(
              (calibration.measured[focus]?.exact6 ??
                calibration.constant_prior_baseline.exact6) * 100
            ).toFixed(0)}
            %. {formatCalibrationNote(calibration, t.watch.calibrationNote)}
          </p>
        )}
      </ExplainModal>

      <ExplainModal
        title={t.watch.analystsModal}
        open={modal === "analysts"}
        onClose={() => setModal(null)}
        closeLabel={t.common.close}
      >
        <ul className="analyst-list modal-analysts">
          {TOP_MODELS.map((m) => {
            const copy = t.models[m.id];
            return (
              <li key={m.id}>
                <span className="analyst-dot" style={{ background: m.color }} />
                <div>
                  <strong style={{ color: m.color }}>{copy.label}</strong>
                  <em>{copy.trait}</em>
                  <p>{copy.blurb}</p>
                  {focus === m.id && (
                    <p className="status">
                      {t.watch.currentFocus} · {focusCopy.label}
                    </p>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      </ExplainModal>
    </div>
  );
}
