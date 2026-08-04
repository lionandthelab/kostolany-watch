/**
 * Emit a static shell per SPA route with correct <title>, description and
 * canonical.
 *
 * Why this exists: `firebase.json` rewrites `**` to `/index.html`, whose head
 * hardcodes the Korean HOME title and `canonical="…/"`. `seo.ts` only corrects
 * it after React hydrates, so a non-rendering crawler — Naver's especially —
 * sees every route as a duplicate of the home page. The per-route SEO the app
 * appears to have was largely notional.
 *
 * Firebase Hosting serves a matching static file before consulting rewrites, so
 * writing `dist/watch/index.html` makes `/watch` resolve to it. The file is the
 * built SPA shell with head tags swapped, so the app still boots normally and
 * `applySeo` reconciles to the same values (idempotent — it upserts by
 * selector).
 *
 * Runs AFTER `vite build` because it needs the hashed asset filenames.
 */

import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const dist = join(root, "dist");
const SITE = "https://kostolany-watch.web.app";

/**
 * Mirrors `t.seo.*` in src/i18n/ko.ts. Duplicated because the source is
 * TypeScript that node cannot import directly — `tests/test_prerender.py`
 * asserts the two stay identical, so drift fails CI rather than shipping.
 */
const ROUTES = [
  {
    path: "/watch",
    title: "국면 — 달걀 위 확률",
    description:
      "S&P 500·비트코인 국면 확률과 세 AI 분석가 위치를 달걀에서 확인합니다. 교육용 국면 인식.",
  },
  {
    path: "/macro",
    title: "거시 흐름",
    description: "금리·고용·심리와 핵심 뉴스로 거시 맥락을 읽습니다. 교육·연구 목적.",
  },
  {
    path: "/news",
    title: "뉴스 데스크",
    description: "돈·금리, 신용, 가상화폐, 심리 관련 헤드라인과 브리핑. 투자 권유 아님.",
  },
  {
    path: "/about",
    title: "서비스 소개",
    description: "Kostolany Watch가 무엇을 하는지, 여섯 국면과 세 AI를 소개합니다.",
  },
];

const SITE_NAME = "Kostolany Watch";

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/** Replace the value of a tag matched by `pattern`, or report failure. */
function swap(html, pattern, replacement, label) {
  if (!pattern.test(html)) throw new Error(`prerender: ${label} not found in dist/index.html`);
  return html.replace(pattern, replacement);
}

function renderRoute(shell, route) {
  const url = `${SITE}${route.path}`;
  // `applySeo` appends the site name the same way — keep them byte-identical so
  // hydration does not visibly rewrite the title.
  const title = route.title.includes(SITE_NAME)
    ? route.title
    : `${route.title} · ${SITE_NAME}`;
  const desc = escapeHtml(route.description);

  let html = shell;
  html = swap(html, /<title>[\s\S]*?<\/title>/, `<title>${escapeHtml(title)}</title>`, "title");
  html = swap(
    html,
    /<meta\s+name="description"\s+content="[\s\S]*?"\s*\/>/,
    `<meta name="description" content="${desc}" />`,
    "description",
  );
  html = swap(
    html,
    /<link rel="canonical" href="[^"]*" \/>/,
    `<link rel="canonical" href="${url}" />`,
    "canonical",
  );
  // hreflang set points at this route, not the home page.
  html = html.replace(
    /<link rel="alternate" hreflang="(ko|en|x-default)" href="[^"]*" \/>/g,
    (_m, lang) => `<link rel="alternate" hreflang="${lang}" href="${url}" />`,
  );
  html = html.replace(
    /<meta property="og:url" content="[^"]*" \/>/,
    `<meta property="og:url" content="${url}" />`,
  );
  html = html.replace(
    /<meta property="og:title" content="[^"]*" \/>/,
    `<meta property="og:title" content="${escapeHtml(title)}" />`,
  );
  html = html.replace(
    /<meta property="og:description" content="[\s\S]*?" \/>/,
    `<meta property="og:description" content="${desc}" />`,
  );
  return html;
}

function main() {
  const shellPath = join(dist, "index.html");
  if (!existsSync(shellPath)) {
    console.error("prerender: dist/index.html missing — run vite build first");
    process.exit(1);
  }
  const shell = readFileSync(shellPath, "utf8");

  for (const route of ROUTES) {
    const outDir = join(dist, route.path.replace(/^\//, ""));
    mkdirSync(outDir, { recursive: true });
    writeFileSync(join(outDir, "index.html"), renderRoute(shell, route), "utf8");
  }
  console.log(`prerender: ${ROUTES.length} route shells → dist/{${ROUTES.map((r) => r.path.slice(1)).join(",")}}`);
}

main();
