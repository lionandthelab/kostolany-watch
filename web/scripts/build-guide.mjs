/**
 * Emit crawlable HTML for /guide/* into public/ and refresh sitemap.xml + RSS.
 * Run via: node scripts/build-guide.mjs (also hooked as prebuild).
 * Drafts (status === "draft") are skipped from public outputs.
 */
import { mkdirSync, writeFileSync, readFileSync, unlinkSync, existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const root = join(__dirname, "..");
const allArticles = JSON.parse(readFileSync(join(root, "src/guide/articles.json"), "utf8"));
const articles = allArticles.filter((a) => a.status !== "draft");
const SITE = "https://kostolany-watch.web.app";

function escapeHtml(s) {
  return String(s)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function escapeXml(s) {
  return String(s)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&apos;");
}

function pageCss() {
  return `
    :root { --ink:#1a1f1c; --muted:#5c6b63; --moss:#2f5d50; --line:#d5ddd6; }
    body { margin:0; font-family:"IBM Plex Sans",system-ui,sans-serif; color:var(--ink); background:linear-gradient(165deg,#f7f3ea,#e7efe4 55%,#dfe8df); line-height:1.65; }
    .wrap { width:min(42rem, calc(100% - 2rem)); margin:0 auto; padding:2rem 0 4rem; }
    a { color:var(--moss); }
    h1 { font-family:Fraunces,Georgia,serif; font-size:clamp(1.6rem,4vw,2.2rem); color:var(--moss); line-height:1.25; margin:0; }
    h2 { font-family:Fraunces,Georgia,serif; font-size:1.25rem; margin-top:1.75rem; }
    .meta { color:var(--muted); font-size:0.9rem; margin:0.5rem 0 1.5rem; }
    .nav { display:flex; gap:1rem; flex-wrap:wrap; margin-bottom:1.5rem; font-size:0.92rem; }
    .disclaimer { color:var(--muted); font-size:0.88rem; border-top:1px solid var(--line); padding-top:1rem; margin-top:2rem; }
    article { background:rgba(255,255,255,0.45); border:1px solid var(--line); border-radius:14px; padding:1.25rem 1.35rem 1.5rem; }
    .cta { display:inline-block; margin-top:1rem; padding:0.55rem 1rem; border-radius:10px; background:var(--moss); color:#fff !important; text-decoration:none; font-weight:600; }
    ul { padding-left: 1.2rem; }
    .push-cta { margin:1.5rem 0; padding:1rem 1.1rem; border:1px solid var(--line); border-radius:12px; background:rgba(255,255,255,0.35); }
    .push-cta h2 { margin:0; font-size:1.15rem; }
    .push-cta .lead { margin:0.35rem 0 0.75rem; color:var(--muted); font-size:0.92rem; }
  `;
}

function pushCtaBlock(lang) {
  const ko = lang !== "en";
  const title = ko ? "일일 국면 알림" : "Daily regime alerts";
  const lead = ko
    ? "브라우저 푸시로 매일 국면 지표를 받을 수 있습니다. 앱 가이드에서 켜 주세요."
    : "Get a daily regime ping via browser push — enable it in the Guide app.";
  const link = ko ? "알림 설정 열기" : "Open alert settings";
  return `
    <section class="push-cta">
      <h2>${title}</h2>
      <p class="lead">${lead}</p>
      <p><a class="cta" href="/guide/">${link}</a></p>
    </section>`;
}

function writeArticle(article, lang) {
  const title = article.title[lang];
  const description = article.description[lang];
  const bodyHtml = article.body[lang];
  const canonical = `${SITE}/guide/${article.slug}/`;
  const page = `<!doctype html>
<html lang="${lang}">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>${escapeHtml(title)} · Kostolany Watch</title>
  <meta name="description" content="${escapeHtml(description)}" />
  <meta name="robots" content="index,follow" />
  <link rel="canonical" href="${canonical}" />
  <link rel="alternate" type="application/rss+xml" title="Kostolany Watch Guide" href="${SITE}/guide/feed.xml" />
  <meta property="og:type" content="article" />
  <meta property="og:site_name" content="Kostolany Watch" />
  <meta property="og:locale" content="${lang === "en" ? "en_US" : "ko_KR"}" />
  <meta property="og:url" content="${canonical}" />
  <meta property="og:title" content="${escapeHtml(title)}" />
  <meta property="og:description" content="${escapeHtml(description)}" />
  <meta property="og:image" content="${SITE}/og-image.jpg" />
  <link rel="icon" href="/favicon.svg" type="image/svg+xml" />
  <style>${pageCss()}</style>
</head>
<body>
  <div class="wrap">
    <nav class="nav">
      <a href="/">Kostolany Watch</a>
      <a href="/guide/">Guide</a>
      <a href="/watch">Regime</a>
      <a href="/macro">Macro</a>
      <a href="/news">News</a>
    </nav>
    <article>
      <h1>${escapeHtml(title)}</h1>
      <p class="meta">${lang === "en" ? "Updated" : "업데이트"} ${escapeHtml(article.date)}</p>
      ${bodyHtml}
      <p><a class="cta" href="/watch">${lang === "en" ? "Open regime view" : "국면 보기"}</a></p>
      ${pushCtaBlock(lang)}
    </article>
  </div>
  <script async src="https://www.googletagmanager.com/gtag/js?id=G-XC1KSGMXTX"></script>
  <script>
    window.dataLayer = window.dataLayer || [];
    function gtag(){dataLayer.push(arguments);}
    gtag('js', new Date());
    gtag('config', 'G-XC1KSGMXTX', { page_path: location.pathname });
  </script>
</body>
</html>`;
  const outDir = join(root, "public/guide", article.slug);
  mkdirSync(outDir, { recursive: true });
  if (lang === "ko") writeFileSync(join(outDir, "index.html"), page, "utf8");
  writeFileSync(join(outDir, `index.${lang}.html`), page, "utf8");
}

function writeGuideIndex() {
  /**
   * Do NOT write public/guide/index.html — Firebase would serve it instead of the SPA,
   * hiding the React newsletter form. Keep article HTML + feed only; /guide/ → SPA.
   */
  const dir = join(root, "public/guide");
  mkdirSync(dir, { recursive: true });
  const stale = join(dir, "index.html");
  if (existsSync(stale)) unlinkSync(stale);
}

function writeSitemap() {
  const core = [
    ["/", "weekly", "1.0"],
    ["/watch", "daily", "0.9"],
    ["/macro", "daily", "0.8"],
    ["/news", "hourly", "0.8"],
    ["/about", "monthly", "0.6"],
    ["/guide/", "weekly", "0.85"],
  ];
  const urls = [
    ...core.map(
      ([path, freq, pri]) => `  <url>
    <loc>${SITE}${path}</loc>
    <changefreq>${freq}</changefreq>
    <priority>${pri}</priority>
  </url>`,
    ),
    ...articles.map(
      (a) => `  <url>
    <loc>${SITE}/guide/${a.slug}/</loc>
    <lastmod>${a.date}</lastmod>
    <changefreq>${a.kind === "weekly" ? "weekly" : "monthly"}</changefreq>
    <priority>0.75</priority>
  </url>`,
    ),
  ];
  writeFileSync(
    join(root, "public/sitemap.xml"),
    `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
${urls.join("\n")}
</urlset>
`,
    "utf8",
  );
}

function stripHtml(html) {
  return String(html)
    .replace(/<[^>]+>/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function writeRss() {
  const items = [...articles]
    .sort((a, b) => (a.date < b.date ? 1 : -1))
    .slice(0, 40)
    .map((a) => {
      const title = escapeXml(a.title.ko);
      const link = `${SITE}/guide/${a.slug}/`;
      const desc = escapeXml(a.description.ko || stripHtml(a.body.ko).slice(0, 280));
      return `    <item>
      <title>${title}</title>
      <link>${link}</link>
      <guid isPermaLink="true">${link}</guid>
      <pubDate>${new Date(a.date + "T09:00:00+09:00").toUTCString()}</pubDate>
      <description>${desc}</description>
    </item>`;
    })
    .join("\n");
  const xml = `<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Kostolany Watch Guide</title>
    <link>${SITE}/guide/</link>
    <description>코스톨라니 달걀·6국면 해설과 주간 국면 브리핑 (교육·연구용, 투자 권유 아님)</description>
    <language>ko</language>
${items}
  </channel>
</rss>
`;
  mkdirSync(join(root, "public/guide"), { recursive: true });
  writeFileSync(join(root, "public/guide/feed.xml"), xml, "utf8");
}

for (const article of articles) {
  writeArticle(article, "ko");
  writeArticle(article, "en");
}
writeGuideIndex();
writeSitemap();
writeRss();
const drafts = allArticles.length - articles.length;
console.log(
  `build-guide: ${articles.length} published` +
    (drafts ? ` (+${drafts} draft skipped)` : "") +
    " → public/guide + sitemap.xml + feed.xml",
);
