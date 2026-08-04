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
import { useLocale, useT } from "./i18n";

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

function pickLocaleText(
  lang: "en" | "ko",
  localized: string | undefined,
  fallback: string | undefined,
): string | undefined {
  const ok = (s: string | undefined) => {
    if (!s) return undefined;
    if (lang === "en" && /[가-힣]/.test(s)) return undefined;
    return s;
  };
  return ok(localized) || ok(fallback);
}

function cardDigits(id: string): number {
  if (id === "btc" || id === "jobs") return 0;
  if (
    id === "fear_greed" ||
    id === "crypto_fear_greed" ||
    id === "vix" ||
    id === "gold" ||
    id === "dxy"
  ) {
    return 1;
  }
  return 2;
}

function MacroCardView({
  card,
  lang,
  inert = false,
}: {
  card: MacroCard;
  lang: "en" | "ko";
  /** Duplicate marquee clone — hide from a11y tree */
  inert?: boolean;
}) {
  const digits = cardDigits(card.id);
  const title =
    pickLocaleText(lang, lang === "ko" ? card.title_ko : card.title_en, card.title) || card.title;
  const blurb = pickLocaleText(lang, lang === "ko" ? card.blurb_ko : card.blurb_en, card.blurb);
  const deltaLabel = pickLocaleText(
    lang,
    lang === "ko" ? card.delta_label_ko : card.delta_label_en,
    card.delta_label,
  );
  const deltaDigits = deltaLabel?.includes("%") ? 1 : deltaLabel ? 0 : 2;
  return (
    <article className="macro-card" aria-hidden={inert || undefined}>
      <header>
        <h3>{title}</h3>
        {blurb ? <p className="macro-card-blurb">{blurb}</p> : null}
      </header>
      <div className="macro-card-value">
        <strong>
          {fmtNum(card.value, digits)}
          {card.unit ? <span className="macro-unit">{card.unit}</span> : null}
        </strong>
        {card.delta != null && (
          <span className="macro-delta">
            {deltaLabel ? `${deltaLabel} ` : ""}
            {card.delta > 0 ? "+" : ""}
            {fmtNum(card.delta, deltaDigits)}
          </span>
        )}
      </div>
      <Sparkline series={card.series} />
    </article>
  );
}

function MacroCardRail({
  cards,
  lang,
  label,
}: {
  cards: MacroCard[];
  lang: "en" | "ko";
  label: string;
}) {
  // Duplicate once for a seamless CSS loop (translate -50%).
  const loop = useMemo(() => [...cards, ...cards], [cards]);
  const durationSec = Math.max(28, cards.length * 5.5);
  return (
    <section className="macro-rail fade-up" aria-label={label}>
      <div className="macro-rail-viewport">
        <div
          className="macro-rail-track"
          style={{ ["--macro-rail-duration" as string]: `${durationSec}s` }}
        >
          {loop.map((c, i) => (
            <MacroCardView
              key={`${c.id}-${i}`}
              card={c}
              lang={lang}
              inert={i >= cards.length}
            />
          ))}
        </div>
      </div>
    </section>
  );
}

export default function MacroDesk() {
  const t = useT();
  const { locale } = useLocale();
  const lang = locale === "en" ? "en" : "ko";
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
    <div className="desk-panel macro-page">
      <header className="desk-hero fade-up">
        <div className="desk-hero-row">
          <h2 className="desk-title">{t.macro.title}</h2>
          <div className="cache-bar desk-hero-meta">
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
            <strong>
              {(lang === "ko" ? fw.label_ko : fw.label_en) || fw.label || "—"}
            </strong>
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

      {board && board.cards.length > 0 && (
        <MacroCardRail cards={board.cards} lang={lang} label={t.macro.gaugesAria} />
      )}

      {(() => {
        const brief =
          lang === "ko"
            ? news?.priority_summary_md_ko || news?.priority_summary_md
            : news?.priority_summary_md || news?.priority_summary_md_ko;
        return brief ? (
          <section className="briefing-rail fade-up" aria-label={t.macro.briefingAria}>
            <MarkdownBrief source={brief} />
          </section>
        ) : null;
      })()}

      {headlines.length > 0 && (
        <section className="macro-news fade-up">
          <div className="macro-news-head">
            <h3 className="desk-subtitle">{t.macro.headlines}</h3>
            <a className="btn-ghost btn-sm" href="/news">
              {t.macro.moreNews}
            </a>
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
                    <span className="news-row-source">
                      {t.news.themeLabels[it.theme as keyof typeof t.news.themeLabels] ??
                        (lang === "ko" ? it.theme_ko : it.theme_en) ??
                        it.source}
                    </span>
                  </span>
                  <span className="news-row-title">{it.title}</span>
                </a>
              </li>
            ))}
          </ul>
        </section>
      )}

      {(board || news) && <p className="disclaimer">{t.common.disclaimer}</p>}
    </div>
  );
}
