import articlesJson from "./articles.json";

export type GuideLang = "ko" | "en";

export type GuideArticle = {
  slug: string;
  date: string;
  kind: "evergreen" | "weekly" | "daily";
  /** Omit or "published" → live; "draft" → local only until publish. */
  status?: "draft" | "published";
  title: Record<GuideLang, string>;
  description: Record<GuideLang, string>;
  body: Record<GuideLang, string>;
};

export const GUIDE_ARTICLES = articlesJson as GuideArticle[];

/** Today in Seoul — the site's publishing day boundary. */
export function kstToday(): string {
  return new Date(Date.now() + 9 * 3600 * 1000).toISOString().slice(0, 10);
}

/**
 * Live = not a draft, and the publish date has arrived.
 *
 * Mirrors `isLive` in scripts/build-guide.mjs deliberately. The build script is
 * the real gate — it decides whether the HTML file and sitemap entry exist at
 * all. This copy stops a cached bundle from listing a link to a page that has
 * not been generated yet, which would be a 404 for the reader.
 */
export function isPublished(article: GuideArticle, today = kstToday()): boolean {
  return article.status !== "draft" && String(article.date || "") <= today;
}

export function getArticle(slug: string): GuideArticle | undefined {
  const a = GUIDE_ARTICLES.find((x) => x.slug === slug);
  if (!a || !isPublished(a)) return undefined;
  return a;
}

export function listArticles(): GuideArticle[] {
  return GUIDE_ARTICLES.filter((a) => isPublished(a)).sort((a, b) => (a.date < b.date ? 1 : -1));
}

export function mergeArticles(
  local: GuideArticle[],
  remote: GuideArticle[],
): GuideArticle[] {
  const map = new Map<string, GuideArticle>();
  for (const a of local.filter((x) => isPublished(x))) map.set(a.slug, a);
  for (const a of remote) {
    if (!a?.slug) continue;
    map.set(a.slug, { ...a, status: "published" });
  }
  return [...map.values()].sort((a, b) => (a.date < b.date ? 1 : -1));
}
