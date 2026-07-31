import { useCallback, useEffect, useState } from "react";
import { fetchNews, type NewsDesk, type NewsTone } from "./api";
import MarkdownBrief from "./MarkdownBrief";
import { useLocale, useT } from "./i18n";
import LocaleSwitcher from "./LocaleSwitcher";
import AdSlot from "./AdSlot";

const THEME_COLOR: Record<string, string> = {
  money: "#2f5d50",
  credit: "#c45c3e",
  crypto: "#b8860b",
  korea: "#4a7c9b",
  sentiment: "#6b7c74",
};

function ToneMeter({
  tone,
  color,
  guard,
  ease,
}: {
  tone: NewsTone;
  color: string;
  guard: string;
  ease: string;
}) {
  const pct = ((tone.score + 1) / 2) * 100;
  return (
    <div className="news-tone" aria-label={tone.label}>
      <div className="news-tone-meta">
        <span className="news-tone-label">{tone.label}</span>
        <span className="news-tone-score">
          {tone.score > 0 ? "+" : ""}
          {(tone.score * 100).toFixed(0)}
        </span>
      </div>
      <div className="news-tone-track">
        <span className="news-tone-mid" />
        <span
          className="news-tone-thumb"
          style={{ left: `${pct}%`, background: color }}
        />
      </div>
      <div className="news-tone-ends">
        <span>{guard}</span>
        <span>{ease}</span>
      </div>
    </div>
  );
}

type Props = {
  onWatch?: () => void;
  onMacro?: () => void;
  onAbout?: () => void;
  onGuide?: () => void;
  /** @deprecated use onMacro */
  onFlows?: () => void;
  onBack?: () => void;
};

export default function NewsDesk({ onWatch, onMacro, onAbout, onGuide, onFlows, onBack }: Props) {
  const t = useT();
  const { formatDate } = useLocale();
  const goMacro = onMacro ?? onFlows;
  const [desk, setDesk] = useState<NewsDesk | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [themeFilter, setThemeFilter] = useState<string>("all");

  const load = useCallback(async (refresh = false) => {
    if (!refresh) setLoading(true);
    setError(null);
    try {
      const data = await fetchNews(refresh);
      setDesk(data);
      if (data.refreshing || data.stale) {
        window.setTimeout(() => {
          void fetchNews(false).then(setDesk).catch(() => undefined);
        }, 4000);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load(false);
  }, [load]);

  const sections =
    desk?.sections.filter((s) => themeFilter === "all" || s.theme === themeFilter) ?? [];

  return (
    <div className="page news-page">
      <nav className="topnav desk-nav">
        <div className="desk-tabs" role="tablist" aria-label={t.nav.screens}>
          <button type="button" className="desk-tab" onClick={onWatch}>
            {t.nav.regime}
          </button>
          <button type="button" className="desk-tab" onClick={goMacro}>
            {t.nav.macro}
          </button>
          <button type="button" className="desk-tab is-active" aria-current="page">
            {t.nav.news}
          </button>
          {onGuide && (
            <button type="button" className="desk-tab" onClick={onGuide}>
              {t.nav.guide}
            </button>
          )}
        </div>
        <div className="desk-nav-end">
          <LocaleSwitcher />
          {(onAbout || onBack) && (
            <button type="button" className="nav-quiet nav-btn" onClick={onAbout ?? onBack}>
              {onAbout ? t.nav.about : t.nav.aboutBack}
            </button>
          )}
        </div>
      </nav>

      <header className="news-hero fade-up">
        <h2 className="section-kicker">{t.news.title}</h2>
        <div className="cache-bar">
          <span className="status">
            {loading
              ? desk
                ? t.common.refreshing
                : t.common.loading
              : formatDate(desk?.asof) || ""}
          </span>
          <button
            type="button"
            className="btn-refresh"
            disabled={loading}
            onClick={() => void load(true)}
          >
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

      {desk && (
        <>
          {desk.priority_summary_md && (
            <section className="briefing-rail fade-up" aria-label={t.news.briefingAria}>
              <MarkdownBrief source={desk.priority_summary_md} />
            </section>
          )}

          <AdSlot className="ad-slot--rail" slot="news-mid" />

          <div className="news-filters" role="tablist" aria-label={t.news.themes}>
            <button
              type="button"
              className={`news-filter${themeFilter === "all" ? " is-active" : ""}`}
              onClick={() => setThemeFilter("all")}
            >
              {t.news.all}
            </button>
            {desk.sections.map((s) => (
              <button
                key={s.theme}
                type="button"
                className={`news-filter${themeFilter === s.theme ? " is-active" : ""}`}
                style={{ ["--chip" as string]: THEME_COLOR[s.theme] }}
                onClick={() => setThemeFilter(s.theme)}
              >
                {s.label_ko}
              </button>
            ))}
          </div>

          {sections.map((sec) => {
            const color = THEME_COLOR[sec.theme] ?? "#2f5d50";
            return (
              <section key={sec.theme} className="news-section fade-up">
                <div className="news-section-head">
                  <h2 className="news-section-title" style={{ color }}>
                    {sec.label_ko}
                  </h2>
                  {sec.tone && (
                    <ToneMeter
                      tone={sec.tone}
                      color={color}
                      guard={t.news.toneGuard}
                      ease={t.news.toneEase}
                    />
                  )}
                </div>
                {sec.summary_ko && <p className="news-section-summary">{sec.summary_ko}</p>}
                {sec.items.length === 0 ? (
                  <p className="status">{t.news.empty}</p>
                ) : (
                  <ul className="news-list">
                    {sec.items.map((it) => (
                      <li key={it.id}>
                        <a
                          className="news-row"
                          href={it.url}
                          target="_blank"
                          rel="noopener noreferrer"
                        >
                          <span className="news-row-meta">
                            <span className="news-row-source">{it.source}</span>
                            {it.published_at && (
                              <time dateTime={it.published_at}>{formatDate(it.published_at)}</time>
                            )}
                          </span>
                          <span className="news-row-title">{it.title}</span>
                        </a>
                      </li>
                    ))}
                  </ul>
                )}
              </section>
            );
          })}

          <section className="news-desk-links fade-up">
            <h2 className="news-section-title">{t.news.official}</h2>
            <ul className="news-official">
              {desk.desk_links.map((d) => (
                <li key={d.url}>
                  <a href={d.url} target="_blank" rel="noopener noreferrer">
                    <span className="news-theme-dot" style={{ background: THEME_COLOR[d.theme] }} />
                    <span className="news-official-title">{d.title}</span>
                    <span className="news-official-src">{d.source}</span>
                  </a>
                </li>
              ))}
            </ul>
          </section>

          <p className="disclaimer">{desk.disclaimer}</p>
        </>
      )}
    </div>
  );
}
