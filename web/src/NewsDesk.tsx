import { useCallback, useEffect, useState } from "react";
import { fetchNews, type NewsDesk } from "./api";
import MarkdownBrief from "./MarkdownBrief";

const THEME_COLOR: Record<string, string> = {
  money: "#2f5d50",
  credit: "#c45c3e",
  korea: "#4a7c9b",
  sentiment: "#6b7c74",
};

function formatKst(iso?: string | null): string {
  if (!iso) return "";
  try {
    return new Date(iso).toLocaleString("ko-KR", {
      timeZone: "Asia/Seoul",
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

type Props = {
  onBack?: () => void;
  onWatch?: () => void;
  onFlows?: () => void;
};

export default function NewsDesk({ onBack, onWatch, onFlows }: Props) {
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
        // soft poll until brief settles
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
        {onBack && (
          <button type="button" className="nav-quiet nav-btn" onClick={onBack}>
            ← 소개
          </button>
        )}
        <div className="desk-tabs" role="tablist" aria-label="화면">
          <button type="button" className="desk-tab" onClick={onWatch}>
            국면
          </button>
          <button type="button" className="desk-tab is-active" aria-current="page">
            뉴스
          </button>
          <button type="button" className="desk-tab" onClick={onFlows}>
            흐름
          </button>
        </div>
      </nav>

      <header className="news-hero fade-up">
        <h2 className="section-kicker">뉴스</h2>
        <div className="cache-bar">
          <span className="status">
            {loading
              ? desk
                ? "갱신 중…"
                : "수집 중…"
              : desk?.refreshing
                ? `백그라운드 갱신 · ${formatKst(desk.asof)}`
                : desk?.cached
                  ? `캐시 · ${formatKst(desk.asof)}`
                  : `갱신 · ${formatKst(desk?.asof)}`}
          </span>
          <button
            type="button"
            className="btn-refresh"
            disabled={loading}
            onClick={() => void load(true)}
          >
            새로고침
          </button>
        </div>
      </header>

      {error && (
        <p className="status">
          오류: {error}{" "}
          <button type="button" className="linkish" onClick={() => void load(false)}>
            다시 시도
          </button>
        </p>
      )}

      {desk && (
        <>
          {desk.priority_summary_md && (
            <section className="briefing-rail fade-up" aria-label="오늘 핵심">
              <MarkdownBrief source={desk.priority_summary_md} />
            </section>
          )}

          <div className="news-filters" role="tablist" aria-label="주제">
            <button
              type="button"
              className={`news-filter${themeFilter === "all" ? " is-active" : ""}`}
              onClick={() => setThemeFilter("all")}
            >
              전체
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

          {sections.map((sec) => (
            <section key={sec.theme} className="news-section fade-up">
              <h2 className="news-section-title" style={{ color: THEME_COLOR[sec.theme] }}>
                {sec.label_ko}
              </h2>
              {sec.items.length === 0 ? (
                <p className="status">헤드라인 없음</p>
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
                            <time dateTime={it.published_at}>{formatKst(it.published_at)}</time>
                          )}
                        </span>
                        <span className="news-row-title">{it.title}</span>
                      </a>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          ))}

          <section className="news-desk-links fade-up">
            <h2 className="news-section-title">공식 데스크</h2>
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
