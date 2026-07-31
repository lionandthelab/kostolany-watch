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

export function isPublished(article: GuideArticle): boolean {
  return article.status !== "draft";
}

export function getArticle(slug: string): GuideArticle | undefined {
  const a = GUIDE_ARTICLES.find((x) => x.slug === slug);
  if (!a || !isPublished(a)) return undefined;
  return a;
}

export function listArticles(): GuideArticle[] {
  return GUIDE_ARTICLES.filter(isPublished).sort((a, b) => (a.date < b.date ? 1 : -1));
}

export function mergeArticles(
  local: GuideArticle[],
  remote: GuideArticle[],
): GuideArticle[] {
  const map = new Map<string, GuideArticle>();
  for (const a of local.filter(isPublished)) map.set(a.slug, a);
  for (const a of remote) {
    if (!a?.slug) continue;
    map.set(a.slug, { ...a, status: "published" });
  }
  return [...map.values()].sort((a, b) => (a.date < b.date ? 1 : -1));
}
