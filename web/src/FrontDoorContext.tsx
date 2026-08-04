import { useEffect, useMemo, useRef, useState } from "react";
import { fetchMacroBoard, fetchNews, type MacroBoard, type NewsDesk } from "./api";
import { useLocale, useT } from "./i18n";

/**
 * Compact macro + headline context below the regime call on the front door.
 *
 * Three rules this component exists to hold, all of them from the design review:
 *
 * 1. It is NOT evidence. The shipped head reads prices only, so the section is
 *    labelled and separated as reference context, and sits *below* the call —
 *    context above a conclusion reads as derivation.
 * 2. No fabricated probabilities. The FedWatch cut/hold/hike numbers are a
 *    logistic over three eyeballed constants that was never measured against a
 *    policy decision; they stay on /macro and never appear beside measured
 *    hit rates.
 * 3. No news tone. The tone score is computed from an unfixed deduper (measured
 *    recall 0.00) and can invert its sign on syndicated copies. Headlines and
 *    links only, with a neutral heading — never "why the regime is X".
 *
 * Loads lazily on scroll so the slowest of three endpoints cannot gate the
 * regime call's first paint (all three share one client-side circuit breaker).
 */

/** Risk-climate reading, in display order. FedWatch is deliberately absent. */
const PRIORITY_IDS = ["rates", "curve", "vix", "hy_oas", "fear_greed"] as const;
const MAX_HEADLINES = 3;

function fmt(v: number | null | undefined, digits: number): string {
  if (v == null || Number.isNaN(v)) return "—";
  return v.toFixed(digits);
}

function digitsFor(id: string): number {
  if (id === "fear_greed" || id === "vix") return 1;
  return 2;
}

export default function FrontDoorContext() {
  const t = useT();
  const { locale } = useLocale();
  const lang = locale === "en" ? "en" : "ko";
  const ref = useRef<HTMLElement | null>(null);
  const [visible, setVisible] = useState(false);
  const [board, setBoard] = useState<MacroBoard | null>(null);
  const [news, setNews] = useState<NewsDesk | null>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el || visible) return;
    if (typeof IntersectionObserver === "undefined") {
      setVisible(true);
      return;
    }
    const io = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          setVisible(true);
          io.disconnect();
        }
      },
      { rootMargin: "200px" },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [visible]);

  useEffect(() => {
    if (!visible) return;
    let alive = true;
    // Failures stay silent: this is secondary context, and an error row here
    // would compete with the regime call for attention.
    void fetchMacroBoard(false)
      .then((b) => alive && setBoard(b))
      .catch(() => undefined);
    void fetchNews(false)
      .then((n) => alive && setNews(n))
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [visible]);

  const cards = useMemo(() => {
    const all = board?.cards ?? [];
    return PRIORITY_IDS.map((id) => all.find((c) => c.id === id)).filter(
      (c): c is NonNullable<typeof c> => Boolean(c) && c!.value != null,
    );
  }, [board]);

  const headlines = useMemo(() => (news?.items ?? []).slice(0, MAX_HEADLINES), [news]);

  if (!cards.length && !headlines.length) {
    return <section ref={ref} className="front-context" aria-hidden="true" />;
  }

  return (
    <section ref={ref} className="front-context fade-up" aria-label={t.frontDoor.title}>
      <div className="front-context-head">
        <h2 className="desk-subtitle">{t.frontDoor.title}</h2>
      </div>
      <p className="front-context-note">{t.frontDoor.note}</p>

      {cards.length > 0 && (
        <ul className="front-macro-grid" data-provenance="observed-series">
          {cards.map((c) => {
            const label =
              (lang === "ko" ? c.title_ko : c.title_en) || c.title || c.id;
            return (
              <li key={c.id}>
                <span className="front-macro-label">{label}</span>
                <strong>
                  {fmt(c.value, digitsFor(c.id))}
                  {c.unit ? <span className="macro-unit">{c.unit}</span> : null}
                </strong>
              </li>
            );
          })}
        </ul>
      )}

      {headlines.length > 0 && (
        <div className="front-news" data-provenance="headline">
          <h3 className="front-news-title">{t.frontDoor.newsTitle}</h3>
          <ul className="news-list">
            {headlines.map((it) => (
              <li key={it.id}>
                <a
                  className="news-row"
                  href={it.url}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  <span className="news-row-title">{it.title}</span>
                </a>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="front-context-links">
        <a className="btn-ghost btn-sm" href="/macro">
          {t.frontDoor.macroMore}
        </a>
        <a className="btn-ghost btn-sm" href="/news">
          {t.frontDoor.newsMore}
        </a>
      </div>
    </section>
  );
}
