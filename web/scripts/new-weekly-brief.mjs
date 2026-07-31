/**
 * Scaffold the next Friday weekly regime brief into articles.json (draft by default).
 *
 * Usage:
 *   node scripts/new-weekly-brief.mjs
 *   node scripts/new-weekly-brief.mjs --date 2026-08-07
 *   node scripts/new-weekly-brief.mjs --publish   # mark as published (still edit body first)
 *
 * Cadence: every Friday (Asia/Seoul). Edit body → npm run build:guide → deploy hosting.
 */
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const root = join(__dirname, "..");
const articlesPath = join(root, "src/guide/articles.json");

function parseArgs(argv) {
  const out = { date: null, publish: false };
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === "--date" && argv[i + 1]) {
      out.date = argv[++i];
    } else if (argv[i] === "--publish") {
      out.publish = true;
    }
  }
  return out;
}

function ymdLocal(d) {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

/** Next Friday strictly after today in local calendar (YYYY-MM-DD). */
function nextFridayIso(from = new Date()) {
  const d = new Date(from.getFullYear(), from.getMonth(), from.getDate());
  const day = d.getDay(); // 0 Sun … 5 Fri
  const add = day === 5 ? 7 : (5 - day + 7) % 7 || 7;
  d.setDate(d.getDate() + add);
  return ymdLocal(d);
}

function weekNumber(articles, date) {
  const weeklies = articles.filter((a) => a.kind === "weekly");
  const existing = weeklies.find((a) => a.date === date || a.slug === `weekly-${date}`);
  if (existing) {
    const m = String(existing.title?.en || "").match(/#(\d+)/);
    return m ? Number(m[1]) : weeklies.length;
  }
  return weeklies.length + 1;
}

function buildArticle(n, date, status) {
  const slug = `weekly-${date}`;
  return {
    slug,
    date,
    kind: "weekly",
    status,
    title: {
      ko: `주간 국면 브리핑 #${n} (${date}) — TODO 제목`,
      en: `Weekly regime brief #${n} (${date}) — TODO title`,
    },
    description: {
      ko: "이번 주 국면·거시·뉴스 읽기 순서. 매매 신호가 아닙니다.",
      en: "This week’s reading order for regime, macro, and news. Not a trade signal.",
    },
    body: {
      ko: `<p>TODO: 한 문단으로 이번 주 관찰 포인트를 적습니다. 숫자의 “정답”이 아니라 <strong>읽는 순서</strong>입니다.</p><h2>이번 주 체크 순서</h2><ol><li><a href="/watch">국면</a> — 미국·가상화폐 위치와 AI 점의 흩어짐.</li><li><a href="/macro">거시 흐름</a> — 금리·고용·심리가 같은 방향인지.</li><li><a href="/news">뉴스</a> — 헤드라인은 재료로만.</li></ol><h2>기억할 한 줄</h2><p>TODO: 이번 주 한 줄.</p><p><a href="/guide">가이드 목록</a> · <a href="/watch">국면 열기</a></p><p class="disclaimer">본 정보는 교육·연구 목적의 국면 인식 보조 자료이며 투자 권유·자문이 아닙니다.</p>`,
      en: `<p>TODO: One paragraph on what to watch this week. About <strong>how to look</strong>, not a precise “correct” call.</p><h2>This week’s order</h2><ol><li><a href="/watch">Regime</a> — US &amp; crypto position; scatter of AI marks.</li><li><a href="/macro">Macro</a> — rates, jobs, sentiment alignment.</li><li><a href="/news">News</a> — headlines as inputs only.</li></ol><h2>One line to keep</h2><p>TODO: one line.</p><p><a href="/guide">All guides</a> · <a href="/watch">Open regime</a></p><p class="disclaimer">For education and research on regime recognition only — not investment advice.</p>`,
    },
  };
}

const args = parseArgs(process.argv.slice(2));
const date = args.date || nextFridayIso();
if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) {
  console.error("Invalid --date; use YYYY-MM-DD");
  process.exit(1);
}

const articles = JSON.parse(readFileSync(articlesPath, "utf8"));
const slug = `weekly-${date}`;
const existingIdx = articles.findIndex((a) => a.slug === slug || (a.kind === "weekly" && a.date === date));
const status = args.publish ? "published" : "draft";
const n = weekNumber(articles, date);
const article = buildArticle(n, date, status);

if (existingIdx >= 0) {
  if (!args.publish && articles[existingIdx].status === "published") {
    console.error(`${slug} already published; refuse to overwrite. Edit articles.json manually.`);
    process.exit(1);
  }
  articles[existingIdx] = { ...articles[existingIdx], ...article, body: articles[existingIdx].body };
  if (args.publish) articles[existingIdx].status = "published";
  else if (!articles[existingIdx].status) articles[existingIdx].status = status;
  console.log(`updated ${slug} (status=${articles[existingIdx].status})`);
} else {
  articles.push(article);
  console.log(`added ${slug} #${n} (status=${status})`);
}

writeFileSync(articlesPath, `${JSON.stringify(articles, null, 2)}\n`, "utf8");
console.log(`
Next steps:
  1. Edit title/body TODOs in src/guide/articles.json
  2. node scripts/new-weekly-brief.mjs --date ${date} --publish
  3. npm run build:guide
  4. Deploy hosting (and notify subscribers when email send is wired)
`);
