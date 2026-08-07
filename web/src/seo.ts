/** Site SEO helpers — keep copy educational, never tip-room / guaranteed returns. */

export const SITE_URL = "https://kostolany-watch.web.app";
export const SITE_NAME = "Kostolany Watch";

export type SeoMode = "home" | "watch" | "macro" | "news" | "about" | "guide";

export type SeoCopy = {
  title: string;
  description: string;
  /** Social share title; falls back to document title when omitted. */
  ogTitle?: string;
  /** Social share description; falls back to meta description when omitted. */
  ogDescription?: string;
};

function upsertMeta(attr: "name" | "property", key: string, content: string) {
  let el = document.head.querySelector(`meta[${attr}="${key}"]`) as HTMLMetaElement | null;
  if (!el) {
    el = document.createElement("meta");
    el.setAttribute(attr, key);
    document.head.appendChild(el);
  }
  el.content = content;
}

function upsertLink(rel: string, href: string, extra?: Record<string, string>) {
  const sel = extra
    ? `link[rel="${rel}"]${Object.entries(extra)
        .map(([k, v]) => `[${k}="${v}"]`)
        .join("")}`
    : `link[rel="${rel}"]`;
  let el = document.head.querySelector(sel) as HTMLLinkElement | null;
  if (!el) {
    el = document.createElement("link");
    el.rel = rel;
    if (extra) for (const [k, v] of Object.entries(extra)) el.setAttribute(k, v);
    document.head.appendChild(el);
  }
  el.href = href;
}

export type SeoOptions = {
  /**
   * Mark the URL as not-for-indexing. Used for a slug that resolves to no
   * article — a scheduled post before its date, or a typo. Those serve the SPA
   * shell with 200 (the catch-all rewrite in firebase.json means Hosting cannot
   * 404 them), so without this they read as duplicates of /guide/.
   *
   * This runs after React mounts, which is too late for a crawler that does not
   * execute JS: it fetched HTML whose head still said `index,follow`. For the
   * slugs we can enumerate — scheduled articles, which live articles link
   * forward to — scripts/prerender-routes.mjs writes a static `noindex` shell
   * so the directive is in the bytes. This stays as the catch-all for slugs
   * that cannot be enumerated (typos, dead inbound links).
   */
  noindex?: boolean;
};

export function applySeo(
  copy: SeoCopy,
  path: string,
  locale: string,
  options: SeoOptions = {},
) {
  const url = `${SITE_URL}${path === "/" ? "/" : path}`;
  upsertMeta(
    "name",
    "robots",
    options.noindex ? "noindex,follow" : "index,follow,max-image-preview:large",
  );
  const title = copy.title.includes(SITE_NAME) ? copy.title : `${copy.title} · ${SITE_NAME}`;
  const ogTitle = copy.ogTitle ?? title;
  const ogDescription = copy.ogDescription ?? copy.description;
  document.title = title;
  upsertMeta("name", "description", copy.description);
  upsertMeta("property", "og:title", ogTitle);
  upsertMeta("property", "og:description", ogDescription);
  upsertMeta("property", "og:url", url);
  upsertMeta("property", "og:type", "website");
  upsertMeta("property", "og:site_name", SITE_NAME);
  upsertMeta("property", "og:image", `${SITE_URL}/og-image.jpg`);
  upsertMeta("property", "og:locale", locale === "en" ? "en_US" : "ko_KR");
  upsertMeta("name", "twitter:card", "summary_large_image");
  upsertMeta("name", "twitter:title", ogTitle);
  upsertMeta("name", "twitter:description", ogDescription);
  upsertMeta("name", "twitter:image", `${SITE_URL}/og-image.jpg`);
  upsertLink("canonical", url);
  upsertLink("alternate", url, { hreflang: locale === "en" ? "en" : "ko" });
  upsertLink("alternate", url, { hreflang: "x-default" });
}
