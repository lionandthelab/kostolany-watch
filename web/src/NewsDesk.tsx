import { useCallback, useEffect, useState } from "react";
import { fetchNews, type NewsDesk, type NewsTone } from "./api";
import MarkdownBrief from "./MarkdownBrief";
import { useLocale, useT } from "./i18n";

const THEME_COLOR: Record<string, string> = {
  money: "#2f5d50",
  credit: "#c45c3e",
  crypto: "#b8860b",
  korea: "#4a7c9b",
  sentiment: "#6b7c74",
};

function toneLabelFor(
  score: number,
  labels: {
    easeStrong: string;
    easeSoft: string;
    mixed: string;
    guardSoft: string;
    guardStrong: string;
  },
): string {
  if (score >= 0.35) return labels.easeStrong;
  if (score <= -0.35) return labels.guardStrong;
  if (score > 0.12) return labels.easeSoft;
  if (score < -0.12) return labels.guardSoft;
  return labels.mixed;
}

function ToneMeter({
  tone,
  color,
  guard,
  ease,
  label,
}: {
  tone: NewsTone;
  color: string;
  guard: string;
  ease: string;
  label: string;
}) {
  const pct = ((tone.score + 1) / 2) * 100;
  return (
    <div className="news-tone" aria-label={label}>
      <div className="news-tone-meta">
        <span className="news-tone-label">{label}</span>
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


export default function NewsDesk() {
  const t = useT();
  const { formatDate, locale } = useLocale();
  const lang = locale === "en" ? "en" : "ko";
  const themeLabel = (theme: string, fallback?: string) =>
    t.news.themeLabels[theme as keyof typeof t.news.themeLabels] ?? fallback ?? theme;
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
    <div className="desk-panel news-page">
      <header className="desk-hero fade-up">
        <div className="desk-hero-row">
          <h2 className="desk-title">{t.news.title}</h2>
          <div className="cache-bar desk-hero-meta">
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
                {themeLabel(s.theme, lang === "ko" ? s.label_ko : s.label_en)}
              </button>
            ))}
          </div>

          {sections.map((sec) => {
            const color = THEME_COLOR[sec.theme] ?? "#2f5d50";
            return (
              <section key={sec.theme} className="news-section fade-up">
                <div className="news-section-head">
                  <h2 className="desk-subtitle" style={{ color }}>
                    {themeLabel(sec.theme, lang === "ko" ? sec.label_ko : sec.label_en)}
                  </h2>
                  {sec.tone && (
                    <ToneMeter
                      tone={sec.tone}
                      color={color}
                      guard={t.news.toneGuard}
                      ease={t.news.toneEase}
                      label={toneLabelFor(sec.tone.score, t.news.toneLabels)}
                    />
                  )}
                </div>
                {((lang === "ko" ? sec.summary_ko : sec.summary_en) ||
                  sec.summary_en ||
                  sec.summary_ko) && (
                  <p className="news-section-summary">
                    {(lang === "ko" ? sec.summary_ko : sec.summary_en) ||
                      sec.summary_en ||
                      sec.summary_ko}
                  </p>
                )}
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
            <h2 className="desk-subtitle">{t.news.official}</h2>
            <ul className="news-official">
              {desk.desk_links.map((d) => (
                <li key={d.url}>
                  <a href={d.url} target="_blank" rel="noopener noreferrer">
                    <span className="news-theme-dot" style={{ background: THEME_COLOR[d.theme] }} />
                    <span className="news-official-title">
                      {(lang === "ko" ? d.title_ko : d.title_en) || d.title}
                    </span>
                    <span className="news-official-src">{d.source}</span>
                  </a>
                </li>
              ))}
            </ul>
          </section>

          <p className="disclaimer">{t.common.disclaimer}</p>
        </>
      )}
    </div>
  );
}
