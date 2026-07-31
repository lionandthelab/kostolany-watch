import { useCallback, useEffect, useMemo, useState } from "react";
import {
  fetchMacroBoard,
  fetchNews,
  type MacroBoard,
  type MacroCard,
  type NewsDesk,
  type FlowPoint,
} from "./api";
import MarkdownBrief from "./MarkdownBrief";
import { useT } from "./i18n";
import LocaleSwitcher from "./LocaleSwitcher";
import AdSlot from "./AdSlot";

type Props = {
  onWatch?: () => void;
  onAbout?: () => void;
  onNews?: () => void;
  onGuide?: () => void;
};

const THEME_COLOR: Record<string, string> = {
  money: "#2f5d50",
  credit: "#c45c3e",
  crypto: "#b8860b",
  korea: "#4a7c9b",
  sentiment: "#6b7c74",
};

function fmtNum(v: number | null | undefined, digits = 2): string {
  if (v == null || Number.isNaN(v)) return "—";
  return v.toFixed(digits);
}

function Sparkline({ series, color = "#2f5d50" }: { series: FlowPoint[]; color?: string }) {
  const W = 160;
  const H = 40;
  const pad = 2;
  const vals = series.map((p) => p.value).filter((v) => Number.isFinite(v));
  if (vals.length < 2) {
    return <div className="macro-spark empty" />;
  }
  const lo = Math.min(...vals);
  const hi = Math.max(...vals);
  const span = Math.max(1e-9, hi - lo);
  const d = vals
    .map((v, i) => {
      const x = pad + ((W - pad * 2) * i) / (vals.length - 1);
      const y = H - pad - ((v - lo) / span) * (H - pad * 2);
      return `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  return (
    <svg className="macro-spark" viewBox={`0 0 ${W} ${H}`} aria-hidden="true">
      <path d={d} fill="none" stroke={color} strokeWidth="1.8" />
    </svg>
  );
}

function MacroCardView({ card }: { card: MacroCard }) {
  const digits = card.id === "jobs" || card.id === "fear_greed" ? 1 : 2;
  return (
    <article className="macro-card">
      <header>
        <h3>{card.title}</h3>
        <p className="macro-card-blurb">{card.blurb}</p>
      </header>
      <div className="macro-card-value">
        <strong>
          {fmtNum(card.value, digits)}
          {card.unit ? <span className="macro-unit">{card.unit}</span> : null}
        </strong>
        {card.delta != null && (
          <span className="macro-delta">
            {card.delta_label ? `${card.delta_label} ` : ""}
            {card.delta > 0 ? "+" : ""}
            {fmtNum(card.delta, card.delta_label ? 0 : 2)}
          </span>
        )}
      </div>
      <Sparkline series={card.series} />
    </article>
  );
}

export default function MacroDesk({ onWatch, onAbout, onNews, onGuide }: Props) {
  const t = useT();
  const [board, setBoard] = useState<MacroBoard | null>(null);
  const [news, setNews] = useState<NewsDesk | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (refresh = false) => {
    if (!refresh) setLoading(true);
    setError(null);
    try {
      const [b, n] = await Promise.all([fetchMacroBoard(refresh), fetchNews(refresh)]);
      setBoard(b);
      setNews(n);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load(false);
  }, [load]);

  const fw = board?.fedwatch;
  const headlines = useMemo(() => {
    const items = news?.items ?? [];
    return items.slice(0, 8);
  }, [news]);

  return (
    <div className="page macro-page">
      <nav className="topnav desk-nav">
        <div className="desk-tabs" role="tablist" aria-label={t.nav.screens}>
          <button type="button" className="desk-tab" onClick={onWatch}>
            {t.nav.regime}
          </button>
          <button type="button" className="desk-tab is-active" aria-current="page">
            {t.nav.macro}
          </button>
          {onNews && (
            <button type="button" className="desk-tab" onClick={onNews}>
              {t.nav.news}
            </button>
          )}
          {onGuide && (
            <button type="button" className="desk-tab" onClick={onGuide}>
              {t.nav.guide}
            </button>
          )}
        </div>
        <div className="desk-nav-end">
          <LocaleSwitcher />
          {onAbout && (
            <button type="button" className="nav-quiet nav-btn" onClick={onAbout}>
              {t.nav.about}
            </button>
          )}
        </div>
      </nav>

      <header className="news-hero fade-up">
        <h2 className="section-kicker">{t.macro.title}</h2>
        <div className="cache-bar">
          <span className="status">
            {loading && !board
              ? t.common.loading
              : board?.asof
                ? `${t.common.asof} ${board.asof}`
                : ""}
          </span>
          <button type="button" className="btn-refresh" disabled={loading} onClick={() => void load(true)}>
            {t.common.refresh}
          </button>
        </div>
      </header>

      {error && (
        <p className="status">
          {t.common.error}: {error}{" "}
          <button type="button" className="linkish" onClick={() => void load(false)}>
            {t.common.retry}
          </button>
        </p>
      )}

      {fw && (
        <section className="fedwatch-panel fade-up" aria-label={t.macro.fedwatch}>
          <div className="fedwatch-head">
            <h3>{t.macro.fedwatch}</h3>
            <strong>{fw.label ?? "—"}</strong>
          </div>
          <div className="fedwatch-bars">
            {(
              [
                [t.macro.cut, fw.cut, "#2f5d50"],
                [t.macro.hold, fw.hold, "#6b7c74"],
                [t.macro.hike, fw.hike, "#c45c3e"],
              ] as const
            ).map(([lab, pct, color]) => (
              <div key={lab} className="fedwatch-bar">
                <span>{lab}</span>
                <div className="evidence-meter">
                  <i style={{ width: `${Math.min(100, Math.max(0, pct ?? 0))}%`, background: color }} />
                </div>
                <em>{pct == null ? "—" : `${pct}%`}</em>
              </div>
            ))}
          </div>
          <p className="fg-history-note">{fw.note}</p>
        </section>
      )}

      <AdSlot className="ad-slot--rail" slot="macro-mid" />

      {board && board.cards.length > 0 && (
        <section className="macro-grid fade-up" aria-label={t.macro.gaugesAria}>
          {board.cards.map((c) => (
            <MacroCardView key={c.id} card={c} />
          ))}
        </section>
      )}

      {news?.priority_summary_md && (
        <section className="briefing-rail fade-up" aria-label={t.macro.briefingAria}>
          <MarkdownBrief source={news.priority_summary_md} />
        </section>
      )}

      {headlines.length > 0 && (
        <section className="macro-news fade-up">
          <div className="macro-news-head">
            <h3 className="news-section-title">{t.macro.headlines}</h3>
            {onNews && (
              <button type="button" className="btn-ghost btn-sm" onClick={onNews}>
                {t.macro.moreNews}
              </button>
            )}
          </div>
          <ul className="news-list">
            {headlines.map((it) => (
              <li key={it.id}>
                <a className="news-row" href={it.url} target="_blank" rel="noopener noreferrer">
                  <span className="news-row-meta">
                    <span
                      className="news-theme-dot"
                      style={{ background: THEME_COLOR[it.theme] ?? "#2f5d50" }}
                    />
                    <span className="news-row-source">{it.theme_ko || it.source}</span>
                  </span>
                  <span className="news-row-title">{it.title}</span>
                </a>
              </li>
            ))}
          </ul>
        </section>
      )}

      {board && <p className="disclaimer">{board.disclaimer}</p>}
      {news && <p className="disclaimer">{news.disclaimer}</p>}
    </div>
  );
}
