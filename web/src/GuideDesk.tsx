import { useEffect, useState } from "react";
import PushOptIn from "./PushOptIn";
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
      // Prefer local evergreen when the active locale already has a body.
      if (local?.body?.[lang]) {
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
  }, [slug, lang]);

  const list = mergeArticles(listArticles(), remote);
  const article =
    remoteArticle ||
    (slug ? list.find((a) => a.slug === slug) : null) ||
    (slug ? getArticle(slug) : null) ||
    null;

  return (
    <div className="desk-panel guide-page">
      {!article && (
        <header className="desk-hero fade-up">
          <h2 className="desk-title">{t.guide.title}</h2>
          <p className="desk-lead">{t.guide.lead}</p>
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
          <h1>{article.title[lang] || article.title.en || article.title.ko}</h1>
          {article.body[lang] ? (
            <div
              className="guide-body"
              dangerouslySetInnerHTML={{ __html: article.body[lang] }}
            />
          ) : (
            <p className="status">{t.guide.missingLocale}</p>
          )}
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
          <PushOptIn source={`guide:${article.slug}`} />
        </article>
      ) : (
        <>
          <PushOptIn source="guide" />
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
                  <strong>{a.title[lang] || a.title.en || a.title.ko}</strong>
                  <span className="guide-list-desc">
                    {a.description[lang] || a.description.en || a.description.ko}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}
