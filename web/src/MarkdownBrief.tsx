import type { ReactNode } from "react";

/** Tiny safe markdown for briefing: ## headings, - bullets, **bold**, [text](url). */
export default function MarkdownBrief({ source }: { source: string }) {
  const blocks = source.replace(/\r\n/g, "\n").trim().split(/\n{2,}/);
  return (
    <div className="md-brief">
      {blocks.map((block, bi) => {
        const lines = block.split("\n").filter((l) => l.trim().length > 0);
        if (!lines.length) return null;
        if (lines.every((l) => /^[-*]\s+/.test(l.trim()))) {
          return (
            <ul key={bi} className="md-brief-list">
              {lines.map((line, li) => (
                <li key={li}>{inline(line.trim().replace(/^[-*]\s+/, ""))}</li>
              ))}
            </ul>
          );
        }
        if (lines.length === 1 && /^##\s+/.test(lines[0].trim())) {
          return (
            <h3 key={bi} className="md-brief-h">
              {inline(lines[0].trim().replace(/^##\s+/, ""))}
            </h3>
          );
        }
        return (
          <p key={bi} className="md-brief-p">
            {lines.map((line, li) => (
              <span key={li}>
                {li > 0 && <br />}
                {inline(line)}
              </span>
            ))}
          </p>
        );
      })}
    </div>
  );
}

function inline(text: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  const re = /(\*\*[^*]+\*\*|\[[^\]]+\]\([^)]+\))/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let key = 0;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) nodes.push(text.slice(last, m.index));
    const tok = m[0];
    if (tok.startsWith("**")) {
      nodes.push(<strong key={key++}>{tok.slice(2, -2)}</strong>);
    } else {
      const lm = tok.match(/^\[([^\]]+)\]\(([^)]+)\)$/);
      if (lm) {
        nodes.push(
          <a key={key++} href={lm[2]} target="_blank" rel="noopener noreferrer" title={lm[1]}>
            {linkLabel(lm[1], lm[2])}
          </a>,
        );
      } else {
        nodes.push(tok);
      }
    }
    last = m.index + tok.length;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return nodes;
}

/** Never paint raw Google News / http URLs as link text (layout overflow). */
function linkLabel(text: string, href: string): string {
  const t = text.trim();
  const looksUrl =
    /^https?:\/\//i.test(t) ||
    t.includes("news.google.com/rss/articles") ||
    t.includes("news.google.com/articles") ||
    t.length > 96;
  if (!looksUrl) return t;
  try {
    const u = new URL(href || t);
    const host = u.hostname.replace(/^www\./, "");
    if (host && !host.includes("news.google")) return host;
  } catch {
    /* fall through */
  }
  return "기사 보기";
}
