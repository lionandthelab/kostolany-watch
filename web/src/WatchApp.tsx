import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { EggChart, type ModelMark } from "./EggChart";
import {
  REGIME_GUIDE,
  TOP_MODELS,
  pctFloor,
  pctMove,
  rimFromProba,
  type ModelId,
  type RegimeCode,
} from "./eggGeometry";
import {
  ensureWatchMarket,
  fetchLedgerRecent,
  fetchWatch,
  peekWatch,
  startWatchWarmup,
  type FlipBlock,
  type HeadDissent,
  type LedgerRecent,
  type RunBlock,
  type Snapshot,
  type RegimeCalibration,
  type VoteBlock,
  type WatchBundle,
} from "./api";
import { useLocale, useT } from "./i18n";
import type { Messages } from "./i18n/types";
import ErrorBoundary from "./ErrorBoundary";
import FrontDoorContext from "./FrontDoorContext";
import { trackEvent } from "./analytics";

/** Regime markets: US equities + crypto (no Korea). */
const MARKETS = [
  { value: "^GSPC", labelKey: "marketUs" as const },
  { value: "BTC-USD", labelKey: "marketCrypto" as const },
] as const;

const MODEL_IDS = TOP_MODELS.map((m) => m.id);

/** Owner decision D3 (2026-08-07): the ledger archive strip waits for ~30
 *  archived days. Flip to true and the panel returns — the API already serves
 *  it. docs/DESK_JUDGMENT_LAYER_2026-08-07.md §8. */
const SHOW_LEDGER_ARCHIVE = false;

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
  // Every percentage floors (spec §0.3). Rounding up is banned outright — §6 #9
  // names 81%(0.8062) as the failure. pctFloor is the single formatter.
  const exactLo = pctFloor(Math.min(...vals.map((v) => v.exact6)));
  const exactHi = pctFloor(Math.max(...vals.map((v) => v.exact6)));
  const exact = exactLo === exactHi ? exactLo : `${exactLo.replace("%", "")}~${exactHi}`;
  // ECE is an error metric, not a claim percentage — decimals stay as-is.
  const eceLo = Math.min(...vals.map((v) => v.ece)).toFixed(2);
  const eceHi = Math.max(...vals.map((v) => v.ece)).toFixed(2);
  const sideHit = cal.momo_floor?.vote_side ?? cal.momo_floor?.side_median ?? 0;
  return fill(tpl, {
    window: cal.window,
    n: cal.n_oos_bars.toLocaleString(),
    exact,
    eceLo,
    eceHi,
    sidePct: pctFloor(sideHit).replace("%", ""),
  });
}

const MARKET_LABEL_KEY: Record<string, "marketUs" | "marketCrypto"> = {
  "^GSPC": "marketUs",
  "BTC-USD": "marketCrypto",
};

/** Other-market call, rendered as codes and counts only — never its rates (§6 #10). */
type CrossCall = {
  labelKey: "marketUs" | "marketCrypto";
  regime: string;
  split: string;
  side: "up" | "down";
};

/**
 * Desk judgment layer (docs/DESK_JUDGMENT_LAYER_2026-08-07.md §6).
 *
 * Everything here is already-served fact: regime codes the server computed,
 * deterministic counts over the rule ledger, and closed-form price distances.
 * No hit rate is rendered in this drawer, which is why it stays safe on
 * unmeasured symbols too. Each section disappears whole when its own block is
 * missing — a pre-v5 cache must degrade to silence, never to placeholders.
 */
function JudgmentDrawer({
  t,
  symbol,
  vote,
  regime,
  headDissent,
  flip,
  run,
}: {
  t: Messages;
  symbol: string;
  vote: VoteBlock | null;
  regime: RegimeCode;
  headDissent: HeadDissent | null;
  flip: FlipBlock | null;
  run: RunBlock | null;
}) {
  const [opened, setOpened] = useState(false);
  const [cross, setCross] = useState<CrossCall | null>(null);
  const [archive, setArchive] = useState<LedgerRecent | null>(null);

  // Lazy on first open: neither the cross-market peek nor the ledger read is
  // worth a request for a drawer nobody expanded. `peek` never triggers a
  // rebuild (api.py returns 204 on a miss), so this cannot stampede Cloud Run.
  useEffect(() => {
    if (!opened) return;
    let alive = true;
    setCross(null);
    const other = MARKETS.find((m) => m.value !== symbol);
    if (other) {
      void peekWatch(other.value, MODEL_IDS, 360)
        .then((res) => {
          if (!alive || res.status !== "hit") return;
          const momo = res.data.analysts.find((a) => a.id === "momo");
          const v = momo?.snapshot?.vote;
          // A miss hides the panel. Falling back to a borrowed or stale value
          // would put another market's number under this market's heading.
          if (!momo || !v) return;
          setCross({
            labelKey: other.labelKey,
            regime: momo.snapshot.regime,
            split: v.split,
            side: v.side,
          });
        })
        .catch(() => undefined);
    }
    // C6 (the archive strip) is held back by owner decision D3, 2026-08-07:
    // three rows is thin product for the cost of a permanent "not scored" line
    // on screen, and C4 answers the same question better. Gated here rather
    // than by deleting the code — /ledger/recent ships and stays exercised, so
    // turning this on after ~30 archived days is one flag, not a rebuild.
    // See docs/DESK_JUDGMENT_LAYER_2026-08-07.md §8.
    if (SHOW_LEDGER_ARCHIVE) {
      void fetchLedgerRecent(14)
        .then((d) => {
          if (alive) setArchive(d);
        })
        .catch(() => undefined);
    }
    return () => {
      alive = false;
    };
  }, [opened, symbol]);

  const ruleLabel = (id: string) =>
    t.conviction.ledgerRules[id as keyof typeof t.conviction.ledgerRules] ?? id;
  const headLabel = (id: string) => t.models[id as ModelId]?.label ?? id;
  const dirWord = (x: number) => (x < 0 ? t.judgment.flip.dirDown : t.judgment.flip.dirUp);
  const tierLabel = (tier: string) =>
    tier === "mixed"
      ? t.judgment.flip.tierMixed
      : (t.conviction.tierName[tier as keyof typeof t.conviction.tierName] ?? tier);

  const rulesAgainst = vote ? vote.rules.filter((r) => r.vote !== vote.side) : [];
  const headsAgainst = (headDissent?.calls ?? []).filter((c) => c.regime !== regime);
  const noDissent = rulesAgainst.length === 0 && headsAgainst.length === 0;

  // Contract says ascending |move_pct|; sorting a copy costs nothing and keeps
  // the ladder correct if a future payload ever ships it unordered.
  const ladder = [...(flip?.rules ?? [])].sort(
    (a, b) => Math.abs(a.move_pct) - Math.abs(b.move_pct),
  );
  const nearest = ladder[0] ?? null;
  const nearestStep =
    nearest ? ((flip?.steps ?? []).find((s) => s.move_pct === nearest.move_pct) ?? null) : null;
  const tierSteps = (flip?.steps ?? []).filter(
    (s) => !nearest || s.move_pct !== nearest.move_pct,
  );

  const showDoubt = Boolean(vote);
  const showFlip = Boolean(flip && ladder.length);
  const showHeads = Boolean(headDissent?.calls.length);
  const showRun = Boolean(run);
  // Cross / archive arrive after the first open, so they cannot decide whether
  // the drawer exists — only the payload-derived sections can.
  if (!showDoubt && !showFlip && !showHeads && !showRun) return null;

  return (
    <details
      className="watch-details judgment-drawer"
      onToggle={(e) => {
        const isOpen = e.currentTarget.open;
        setOpened((prev) => prev || isOpen);
      }}
    >
      <summary>{t.judgment.title}</summary>
      <div className="watch-details-body">
        {showDoubt && vote && (
          <section className="judgment-section">
            <h4>{t.judgment.doubt.title}</h4>
            <ul className="judgment-list">
              <li>
                {rulesAgainst.length
                  ? fill(t.judgment.doubt.rules, {
                      k: String(rulesAgainst.length),
                      list: rulesAgainst.map((r) => ruleLabel(r.id)).join(" · "),
                    })
                  : t.judgment.doubt.rulesNone}
              </li>
              {headDissent && (
                <li>
                  {headsAgainst.length
                    ? fill(t.judgment.doubt.heads, {
                        n: String(headDissent.n_heads),
                        k: String(headsAgainst.length),
                        list: headsAgainst
                          .map((c) => `${headLabel(c.id)} — ${c.regime}`)
                          .join(" · "),
                      })
                    : fill(t.judgment.doubt.headsNone, { n: String(headDissent.n_heads) })}
                </li>
              )}
              {flip?.side_flip && (
                <li>
                  {fill(t.judgment.doubt.flip, {
                    d: pctMove(flip.side_flip.move_pct),
                    dir:
                      flip.side_flip.move_pct < 0
                        ? t.judgment.doubt.dirDown
                        : t.judgment.doubt.dirUp,
                    regimeTo: flip.side_flip.regime_to,
                  })}
                </li>
              )}
              {noDissent && <li>{t.judgment.doubt.none}</li>}
            </ul>
            <p className="judgment-note">{t.judgment.doubt.note}</p>
          </section>
        )}

        {showFlip && flip && nearest && (
          <section className="judgment-section">
            <h4>{t.judgment.flip.title}</h4>
            <p className="judgment-lead">{t.judgment.flip.lead}</p>
            <ul className="judgment-list">
              <li>
                {fill(nearestStep ? t.judgment.flip.rule : t.judgment.flip.ruleNoSplit, {
                  d: pctMove(nearest.move_pct),
                  dir: dirWord(nearest.move_pct),
                  ruleLabel: ruleLabel(nearest.id),
                  side: t.conviction.sideWord[nearest.vote === "up" ? "down" : "up"],
                  split: nearestStep?.split ?? "",
                })}
              </li>
              {tierSteps.map((s) => (
                <li key={`${s.split}-${s.move_pct}`}>
                  {fill(t.judgment.flip.tier, {
                    d: pctMove(s.move_pct),
                    dir: dirWord(s.move_pct),
                    tierName: tierLabel(s.tier),
                  })}
                </li>
              ))}
              {flip.side_flip && (
                <li>
                  {fill(t.judgment.flip.side, {
                    d: pctMove(flip.side_flip.move_pct),
                    dir: dirWord(flip.side_flip.move_pct),
                    regimeTo: flip.side_flip.regime_to,
                  })}
                </li>
              )}
            </ul>
            <p className="judgment-note">{t.judgment.flip.note1}</p>
            <p className="judgment-note">{t.judgment.flip.note2}</p>
          </section>
        )}

        {showHeads && headDissent && (
          <section className="judgment-section">
            <h4>{t.judgment.heads.title}</h4>
            <ul className="judgment-heads">
              {headDissent.calls.map((c) => {
                const split =
                  headDissent.side.majority != null && c.side !== headDissent.side.majority;
                return (
                  <li key={c.id} className={split ? "is-dissent" : undefined}>
                    <span>
                      {fill(t.judgment.heads.row, {
                        label: headLabel(c.id),
                        regime: c.regime,
                        side: t.conviction.sideWord[c.side],
                      })}
                    </span>
                    {split && <em>{t.judgment.heads.dissentMark}</em>}
                  </li>
                );
              })}
            </ul>
            <p className="judgment-agree">
              {headDissent.side.majority
                ? fill(t.judgment.heads.agree, {
                    n: String(headDissent.n_heads),
                    k: String(headDissent.side.n_agree),
                    side: t.conviction.sideWord[headDissent.side.majority],
                  })
                : fill(t.judgment.heads.tied, { n: String(headDissent.n_heads) })}
            </p>
            <p className="judgment-note">{t.judgment.heads.note}</p>
          </section>
        )}

        {showRun && run && (
          <section className="judgment-section">
            <h4>{t.judgment.run.title}</h4>
            <ul className="judgment-list">
              <li>
                {fill(run.side_truncated ? t.judgment.run.sideTruncated : t.judgment.run.side, {
                  side: t.conviction.sideWord[run.side],
                  n: String(run.side_bars),
                  since: run.side_since,
                })}
              </li>
              <li>
                {fill(
                  run.regime_truncated ? t.judgment.run.regimeTruncated : t.judgment.run.regime,
                  {
                    regime: run.regime,
                    n: String(run.regime_bars),
                    since: run.regime_since,
                  },
                )}
              </li>
            </ul>
            <p className="judgment-note">{t.judgment.run.note1}</p>
            <p className="judgment-note">{t.judgment.run.note2}</p>
          </section>
        )}

        {cross && (
          <section className="judgment-section">
            <h4>{t.judgment.cross.title}</h4>
            <ul className="judgment-list">
              <li>
                {fill(t.judgment.cross.row, {
                  market: t.watch[cross.labelKey],
                  regime: cross.regime,
                  split: cross.split,
                  side: t.conviction.sideWord[cross.side],
                })}
              </li>
            </ul>
            <p className="judgment-note">{t.judgment.cross.note}</p>
          </section>
        )}

        {archive && (
          <section className="judgment-section">
            <h4>{t.judgment.archive.title}</h4>
            {archive.days.length ? (
              <ul className="judgment-list judgment-archive">
                {archive.days.map((d) => (
                  <li key={d.date}>
                    {fill(t.judgment.archive.row, {
                      date: d.date,
                      cells: d.calls
                        .map((c) => {
                          const key = MARKET_LABEL_KEY[c.symbol];
                          const market = key ? t.watch[key] : c.symbol;
                          return c.split
                            ? fill(t.judgment.archive.cell, {
                                market,
                                regime: c.regime,
                                split: c.split,
                              })
                            : fill(t.judgment.archive.cellPlain, { market, regime: c.regime });
                        })
                        .join(" · "),
                    })}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="judgment-note">{t.judgment.archive.empty}</p>
            )}
            {/* Hard constraint of the design: the unscored notice and the
                pre-registration reference travel with every archive render. */}
            {!archive.scored && (
              <p className="judgment-note">
                {t.judgment.archive.notScored}{" "}
                {archive.prereg_doc ? <code>{archive.prereg_doc}</code> : null}
              </p>
            )}
            {archive.first_date && (
              <p className="judgment-note">
                {fill(t.judgment.archive.range, {
                  first: archive.first_date,
                  n: String(archive.n_days),
                })}
              </p>
            )}
          </section>
        )}
      </div>
    </details>
  );
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
  const [headDissent, setHeadDissent] = useState<HeadDissent | null>(null);
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
    setHeadDissent(bundle.head_dissent ?? null);
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

  // Server-side tally of each head's OWN call (`snapshot.regime`, an argmax).
  // This used to recompute agreement from `rimFromProba` — the circular mean of
  // the posterior — which is a different quantity and could disagree with the
  // head's own label on a spread posterior. rimFromProba now stays on the egg,
  // where the rim angle is what it actually means. Absent block → no line.
  const agreement = useMemo(() => {
    if (!headDissent || headDissent.n_heads < 2) return null;
    const majority = headDissent.regime.majority;
    if (!majority) return null;
    return {
      regime: majority as RegimeCode,
      n: headDissent.regime.n_agree,
      total: headDissent.n_heads,
    };
  }, [headDissent]);

  const contextGauges = useMemo(() => {
    const level = (v: number) =>
      v < 0.35 ? t.watch.levelLow : v < 0.65 ? t.watch.levelMid : t.watch.levelHigh;
    const gaugeLabel = (key: string, fallback: string) =>
      key in t.watch.gauges ? t.watch.gauges[key as keyof typeof t.watch.gauges] : fallback;
    if (focusSnap?.context_gauges?.length) {
      return focusSnap.context_gauges.map((e) => ({
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

  // [4.5] Counts only — no percentage. The §0.7 number budget is a % budget;
  // counts are permitted by §0.4 and the alignment badge is the precedent.
  const judgmentSummary = useMemo(() => {
    const parts: string[] = [];
    const sideMajority = headDissent?.side.majority;
    if (headDissent && sideMajority) {
      parts.push(
        fill(t.judgment.summary.heads, {
          n: String(headDissent.n_heads),
          k: String(headDissent.side.n_agree),
          side: t.conviction.sideWord[sideMajority],
        }),
      );
    }
    const against = vote ? vote.rules.filter((r) => r.vote !== vote.side).length : 0;
    if (against > 0) parts.push(fill(t.judgment.summary.rules, { k: String(against) }));
    const run = focusSnap?.run ?? null;
    if (run) {
      parts.push(
        fill(run.side_truncated ? t.judgment.summary.runTruncated : t.judgment.summary.run, {
          side: t.conviction.sideWord[run.side],
          n: String(run.side_bars),
        }),
      );
    }
    return parts.length ? parts.join(" · ") : null;
  }, [headDissent, vote, focusSnap, t]);

  const judgmentDrawer = (
    <JudgmentDrawer
      t={t}
      symbol={symbol}
      vote={vote}
      regime={regime}
      headDissent={headDissent}
      flip={focusSnap?.flip ?? null}
      run={focusSnap?.run ?? null}
    />
  );

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
                  {judgmentSummary && <p className="judgment-summary">{judgmentSummary}</p>}

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
                  {/* Sibling of the existing drawer — the shipped one keeps its
                      contents and order untouched (design §6.1). */}
                  {judgmentDrawer}
                </div>
              )}

              {contextGauges.length > 0 && (
                <>
                  {/* The shipped head reads prices only. Saying so out loud is the
                      whole guardrail — these meters come from the FRED feature
                      matrix and are not inputs to the call above. */}
                  <p className="context-note">{t.watch.contextNote}</p>
                  <ul className="gauge-strip">
                    {contextGauges.map((e) => (
                      <li key={e.key} title={e.detail || undefined}>
                        <span className="gauge-label">{e.label}</span>
                        <div className="gauge-meter">
                          <i style={{ width: `${Math.min(100, Math.max(0, e.value * 100))}%` }} />
                        </div>
                        <em>{e.level}</em>
                      </li>
                    ))}
                  </ul>
                </>
              )}

              {!(focus === "momo" && vote) && (
                <>
                  {judgmentSummary && <p className="judgment-summary">{judgmentSummary}</p>}
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
                  {judgmentDrawer}
                </>
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

      {/* Below the call, never above it — context placed first reads as cause.
          Own boundary so a bad macro/news payload cannot take the egg with it. */}
      <ErrorBoundary section="front-context" fallback={null}>
        <FrontDoorContext />
      </ErrorBoundary>

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
                    <span className="guide-pct">{pctFloor(p)}</span>
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
            {pctFloor(
              calibration.measured[focus]?.exact6 ??
                calibration.constant_prior_baseline.exact6,
            )}
            . {formatCalibrationNote(calibration, t.watch.calibrationNote)}
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
