import { useEffect, useState } from "react";
import LocaleSwitcher from "./LocaleSwitcher";
import NewsletterSignup from "./NewsletterSignup";
import { fetchBrief, fetchBriefs } from "./api";
import {
  listArticles,
  getArticle,
  mergeArticles,
  type GuideArticle,
} from "./guide/catalog";
import { useLocale, useT } from "./i18n";
import { trackEvent } from "./analytics";

type Props = {
  slug?: string | null;
  onWatch?: () => void;
  onMacro?: () => void;
  onNews?: () => void;
  onAbout?: () => void;
  onGuideHome?: () => void;
  onOpenArticle?: (slug: string) => void;
};

function kindLabel(kind: GuideArticle["kind"], locale: string) {
  if (locale === "en") {
    if (kind === "weekly") return "Weekly";
    if (kind === "daily") return "Daily";
    return "Guide";
  }
  if (kind === "weekly") return "주간";
  if (kind === "daily") return "데일리";
  return "가이드";
}

function remoteToArticle(r: {
  slug: string;
  kind: string;
  date: string;
  title: { ko: string; en: string };
  description?: { ko: string; en: string };
  body?: { ko: string; en: string };
}): GuideArticle {
  const kind = r.kind === "daily" || r.kind === "weekly" ? r.kind : "weekly";
  return {
    slug: r.slug,
    date: r.date,
    kind,
    status: "published",
    title: r.title,
    description: r.description || r.title,
    body: r.body || { ko: "", en: "" },
  };
}

export default function GuideDesk({
  slug,
  onWatch,
  onMacro,
  onNews,
  onAbout,
  onGuideHome,
  onOpenArticle,
}: Props) {
  const t = useT();
  const { locale } = useLocale();
  const lang = locale === "en" ? "en" : "ko";
  const [remote, setRemote] = useState<GuideArticle[]>([]);
  const [remoteArticle, setRemoteArticle] = useState<GuideArticle | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const items = await fetchBriefs(60);
      if (cancelled) return;
      setRemote(items.map(remoteToArticle));
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!slug) {
      setRemoteArticle(null);
      return;
    }
    // Always prefer live API briefs for weekly/daily slugs (local evergreen
    // leftovers / empty stubs must not block the news card).
    const preferRemote = /^(weekly|daily)-/.test(slug);
    if (!preferRemote) {
      const local = getArticle(slug);
      if (local?.body?.ko) {
        setRemoteArticle(null);
        return;
      }
    }
    let cancelled = false;
    (async () => {
      const full = await fetchBrief(slug);
      if (cancelled || !full?.body) return;
      setRemoteArticle(remoteToArticle(full));
    })();
    return () => {
      cancelled = true;
    };
  }, [slug]);

  const list = mergeArticles(listArticles(), remote);
  const article =
    remoteArticle ||
    (slug ? list.find((a) => a.slug === slug) : null) ||
    (slug ? getArticle(slug) : null) ||
    null;

  return (
    <div className="page guide-page">
      <nav className="topnav desk-nav">
        <div className="desk-tabs" role="tablist" aria-label={t.nav.screens}>
          <button type="button" className="desk-tab" onClick={onWatch}>
            {t.nav.regime}
          </button>
          <button type="button" className="desk-tab" onClick={onMacro}>
            {t.nav.macro}
          </button>
          <button type="button" className="desk-tab" onClick={onNews}>
            {t.nav.news}
          </button>
          <button type="button" className="desk-tab is-active" aria-current="page">
            {t.nav.guide}
          </button>
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

      {!article && (
        <header className="news-hero fade-up">
          <h2 className="section-kicker">{t.guide.title}</h2>
          <p className="guide-lead">{t.guide.lead}</p>
          <p className="guide-cadence">
            {t.guide.cadence}{" "}
            <a href="/guide/feed.xml" target="_blank" rel="noreferrer">
              {t.guide.rss}
            </a>
          </p>
        </header>
      )}

      {article ? (
        <article className="guide-article fade-up">
          <button type="button" className="linkish guide-back" onClick={onGuideHome}>
            {t.guide.back}
          </button>
          <p className="guide-kicker">
            {kindLabel(article.kind, lang)} · {article.date}
          </p>
          <h1>{article.title[lang]}</h1>
          <div
            className="guide-body"
            dangerouslySetInnerHTML={{ __html: article.body[lang] || article.body.ko }}
          />
          <button
            type="button"
            className="btn-primary"
            onClick={() => {
              trackEvent("guide_cta_watch", { slug: article.slug });
              onWatch?.();
            }}
          >
            {t.landing.ctaWatch}
          </button>
          <NewsletterSignup source={`guide:${article.slug}`} />
        </article>
      ) : (
        <>
          <NewsletterSignup source="guide" />
          <ul className="guide-list fade-up">
            {list.map((a) => (
              <li key={a.slug}>
                <button
                  type="button"
                  className="guide-list-item"
                  onClick={() => {
                    trackEvent("guide_open", { slug: a.slug });
                    onOpenArticle?.(a.slug);
                  }}
                >
                  <span className="guide-list-meta">
                    {kindLabel(a.kind, lang)} · {a.date}
                  </span>
                  <strong>{a.title[lang]}</strong>
                  <span className="guide-list-desc">{a.description[lang]}</span>
                </button>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}
