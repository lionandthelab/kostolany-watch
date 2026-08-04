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
    h1: "지금 시장은 달걀의 어디쯤인가",
    intro: [
      "코스톨라니 달걀은 시장 사이클을 상승 3구간(A1·A2·A3)과 하락 3구간(B1·B2·B3)으로 나눈 그림입니다. 이 화면은 S&P 500과 비트코인이 그 여섯 칸 중 어디에 가까운지를 확률로 보여 줍니다.",
      "기본 판정은 학습된 AI가 아니라 <strong>사전에 공개된 8개 추세 규칙의 다수결</strong>입니다. 이동평균 5개(20·40·60·100·200일)와 수익률 부호 3개(10·20·60일)를 세어 상승 레그인지 하락 레그인지를 가릅니다. 규칙은 전부 화면에 공개되며 누구나 아무 차트 앱으로 재현할 수 있습니다.",
      "AI 세 종(리듬이·눈치왕·파도꾼)은 참고 시점으로 함께 표시되지만 기본 판정을 대체하지 않습니다.",
    ],
    bullets: [
      "여섯 국면: A1 수정(상승) · A2 동행(상승) · A3 과장(상승) · B1 수정(하락) · B2 동행(하락) · B3 과장(하락)",
      "표시되는 모든 적중률은 워크포워드 표본외 구간에서 금본위 라벨로 채점한 과거 빈도입니다",
      "미래 가격이나 수익률을 예측하지 않으며, 매매 신호가 아닙니다",
    ],
  },
  {
    path: "/macro",
    title: "거시 흐름",
    description: "금리·고용·심리와 핵심 뉴스로 거시 맥락을 읽습니다. 교육·연구 목적.",
    h1: "금리·고용·심리로 읽는 거시 맥락",
    intro: [
      "거시 흐름 화면은 미국 시장의 배경을 이루는 지표를 한자리에 모아 보여 줍니다. 기준금리, 장단기 금리차, 10년물 국채금리, 소비자물가, 실업률, 하이일드 스프레드, 기대인플레이션, VIX, 달러지수, 금과 비트코인 시세, 그리고 공포·탐욕 지수입니다.",
      "각 지표는 최근 추이를 함께 표시해, 값 하나가 아니라 방향을 볼 수 있게 했습니다. 원본은 미국 세인트루이스 연준의 FRED 데이터입니다.",
      "여기 지표들은 <strong>국면 판정에 사용되지 않습니다.</strong> 국면 판정은 가격만 사용하며, 거시 지표는 시장 배경을 함께 보기 위한 참고 자료입니다.",
    ],
    bullets: [
      "금리·물가 흐름: 기준금리, 장단기 금리차, 10년물, 소비자물가, 기대인플레이션",
      "위험 신호: 하이일드 스프레드, VIX, 달러지수",
      "정책 기운 표시는 단기금리와 기준금리 격차를 고정 공식으로 변환한 교육용 근사치이며, 실제 정책 결정 확률로 측정된 값이 아닙니다",
    ],
  },
  {
    path: "/news",
    title: "뉴스 데스크",
    description: "돈·금리, 신용, 가상화폐, 심리 관련 헤드라인과 브리핑. 투자 권유 아님.",
    h1: "돈·신용·가상화폐·심리 헤드라인",
    intro: [
      "뉴스 데스크는 시장 배경을 이루는 헤드라인을 주제별로 모읍니다. 미국 연준과 유럽중앙은행의 공식 발표문을 포함해, 금리·신용·가상화폐·투자심리 관련 기사를 함께 봅니다.",
      "여기서는 기사를 해석하거나 시장 방향을 판단하지 않습니다. 원문 출처로 바로 이동할 수 있는 링크만 제공하며, 판단은 읽는 사람 몫입니다.",
    ],
    bullets: [
      "돈·금리 — 중앙은행 발표와 정책금리 관련 보도",
      "신용·위험 — 회사채 스프레드와 신용 여건",
      "가상화폐 — 비트코인과 디지털자산 관련 소식",
      "심리·위험선호 — 투자심리와 위험자산 선호 흐름",
    ],
  },
  {
    path: "/about",
    title: "서비스 소개",
    description: "Kostolany Watch가 무엇을 하는지, 여섯 국면과 세 AI를 소개합니다.",
    h1: "Kostolany Watch는 무엇을 하나요",
    intro: [
      "Kostolany Watch는 앙드레 코스톨라니의 달걀 모형을 빌려, 시장이 사이클의 어디쯤에 있는지를 확률로 보여 주는 <strong>교육·연구용</strong> 도구입니다.",
      "이 도구가 다른 점은 성능을 부풀리지 않는다는 것입니다. 화면의 모든 적중률은 워크포워드 표본외 구간에서 실제로 측정한 과거 빈도이고, 측정되지 않은 시장에서는 아무 수치도 표시하지 않습니다. 기본 판정 규칙 8개는 전부 공개되어 있어 누구나 검증할 수 있습니다.",
      "반대로 이 도구가 <strong>하지 않는 것</strong>도 분명합니다. 가격이나 수익률을 예측하지 않고, 매수·매도 시점을 알려 주지 않으며, 표시 확률이 미래에 대한 확률이라고 주장하지 않습니다.",
    ],
    bullets: [
      "대상 시장: S&P 500(미국)과 비트코인",
      "기본 판정: 학습 파라미터가 0개인 8개 추세 규칙의 다수결",
      "AI 3종(리듬이·눈치왕·파도꾼)은 참고 시점으로만 표시",
      "투자 권유·자문이 아니며, 투자 판단과 손실 책임은 이용자 본인에게 있습니다",
    ],
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
  return html.replace(/<div id="root">\s*<\/div>/, renderIntro(route));
}

/**
 * Crawlable body for the route, placed inside `#root`.
 *
 * `createRoot().render()` replaces the container's children on mount, so this
 * is a progressive-enhancement fallback: crawlers that do not execute JS (Naver
 * and Bing largely do not) get real text instead of an empty div, and a human
 * on a slow connection sees the explanation rather than a blank page.
 *
 * NOTHING MEASURED GOES HERE. A hit rate baked into static HTML would be a
 * second source for a number that must come from the calibration artifact
 * (spec §0.1) and would silently go stale against it. Evergreen prose only.
 */
function renderIntro(route) {
  const paras = route.intro.map((p) => `        <p>${p}</p>`).join("\n");
  const bullets = route.bullets
    .map((b) => `          <li>${b}</li>`)
    .join("\n");
  return `<div id="root">
      <main class="prerender-intro">
        <h1>${escapeHtml(route.h1)}</h1>
${paras}
        <ul>
${bullets}
        </ul>
        <p class="prerender-nav">
          <a href="/watch">국면</a> · <a href="/macro">거시 흐름</a> ·
          <a href="/news">뉴스</a> · <a href="/guide/">가이드</a>
        </p>
        <p class="prerender-disclaimer">본 정보는 교육·연구 목적의 국면 인식 보조 자료이며 투자 권유·자문이 아닙니다. 투자 판단과 손실에 대한 책임은 이용자 본인에게 있습니다.</p>
      </main>
    </div>`;
}

function main() {
  const shellPath = join(dist, "index.html");
  if (!existsSync(shellPath)) {
    console.error("prerender: dist/index.html missing — run vite build first");
    process.exit(1);
  }
  const shell = readFileSync(shellPath, "utf8");

  mkdirSync(dist, { recursive: true });
  for (const route of ROUTES) {
    // Flat `<route>.html`, NOT `<route>/index.html`: a directory makes Firebase
    // 301 `/watch` → `/watch/`, and every internal link, the sitemap and the
    // SPA router all use the slash-less form. An explicit rewrite in
    // firebase.json maps the route to this file with no redirect hop.
    const name = `${route.path.replace(/^\//, "")}.html`;
    writeFileSync(join(dist, name), renderRoute(shell, route), "utf8");
  }
  console.log(
    `prerender: ${ROUTES.length} route shells → dist/{${ROUTES.map((r) => `${r.path.slice(1)}.html`).join(",")}}`,
  );
}

main();
