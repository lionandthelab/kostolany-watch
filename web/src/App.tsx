import { useCallback, useEffect, useState } from "react";
import MacroDesk from "./MacroDesk";
import Landing from "./Landing";
import NewsDesk from "./NewsDesk";
import WatchApp from "./WatchApp";
import GuideDesk from "./GuideDesk";
import DeskShell, { type DeskTab } from "./DeskShell";
import { trackEvent, trackPageView } from "./analytics";
import { getArticle } from "./guide/catalog";
import { useLocale, useT } from "./i18n";
import { applySeo, type SeoMode } from "./seo";

type Mode = SeoMode;

const HASH_MAP: Record<string, string> = {
  "#watch": "/watch",
  "#macro": "/macro",
  "#flows": "/macro",
  "#news": "/news",
  "#about": "/about",
  "#landing": "/about",
  "#guide": "/guide",
};

function parsePath(): { mode: Mode; guideSlug: string | null } {
  const path = window.location.pathname.replace(/\/+$/, "") || "/";
  if (path === "/watch") return { mode: "watch", guideSlug: null };
  if (path === "/macro") return { mode: "macro", guideSlug: null };
  if (path === "/news") return { mode: "news", guideSlug: null };
  if (path === "/about") return { mode: "about", guideSlug: null };
  if (path === "/guide") return { mode: "guide", guideSlug: null };
  if (path.startsWith("/guide/")) {
    const slug = path.slice("/guide/".length).split("/")[0] || null;
    return { mode: "guide", guideSlug: slug };
  }
  return { mode: "home", guideSlug: null };
}

function pathFor(mode: Mode, guideSlug?: string | null): string {
  if (mode === "watch") return "/watch";
  if (mode === "macro") return "/macro";
  if (mode === "news") return "/news";
  if (mode === "about") return "/about";
  if (mode === "guide") return guideSlug ? `/guide/${guideSlug}` : "/guide";
  return "/";
}

function migrateHashIfNeeded() {
  const h = window.location.hash;
  if (!h) return;
  const next = HASH_MAP[h];
  if (next) window.history.replaceState(null, "", next);
}

export default function App() {
  const t = useT();
  const { locale } = useLocale();
  const initial = typeof window !== "undefined" ? (() => {
    migrateHashIfNeeded();
    return parsePath();
  })() : { mode: "home" as Mode, guideSlug: null };

  const [mode, setMode] = useState<Mode>(initial.mode);
  const [guideSlug, setGuideSlug] = useState<string | null>(initial.guideSlug);

  const go = useCallback((next: Mode, source = "nav", slug: string | null = null) => {
    setMode(next);
    setGuideSlug(next === "guide" ? slug : null);
    const path = pathFor(next, next === "guide" ? slug : null);
    if (window.location.pathname.replace(/\/+$/, "") !== path.replace(/\/+$/, "")) {
      window.history.pushState(null, "", path.endsWith("/") && path !== "/" ? path : path);
    }
    window.scrollTo(0, 0);
    trackEvent("navigate", { screen: next, source, slug: slug || undefined });
  }, []);

  const enterWatch = useCallback(() => go("watch", "cta_or_nav"), [go]);
  const enterMacro = useCallback(() => go("macro", "nav"), [go]);
  const enterNews = useCallback(() => go("news", "nav"), [go]);
  const enterAbout = useCallback(() => go("about", "nav"), [go]);
  const enterHome = useCallback(() => go("home", "nav"), [go]);
  const enterGuide = useCallback(() => go("guide", "nav", null), [go]);
  const openGuideArticle = useCallback((slug: string) => go("guide", "guide_list", slug), [go]);

  useEffect(() => {
    migrateHashIfNeeded();
    const onPop = () => {
      const p = parsePath();
      setMode(p.mode);
      setGuideSlug(p.guideSlug);
    };
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  useEffect(() => {
    const path = pathFor(mode, guideSlug);
    if (mode === "guide" && guideSlug) {
      const art = getArticle(guideSlug);
      const lang = locale === "en" ? "en" : "ko";
      if (art) {
        applySeo(
          { title: art.title[lang], description: art.description[lang] },
          `/guide/${guideSlug}/`,
          locale,
        );
        trackPageView(`/guide/${guideSlug}/`, art.title[lang]);
        return;
      }
    }
    if (mode === "guide") {
      applySeo(t.seo.guide, "/guide/", locale);
      trackPageView("/guide/", t.seo.guide.title);
      return;
    }
    const key = mode === "home" ? "home" : mode;
    applySeo(t.seo[key], pathFor(mode), locale);
    trackPageView(path, document.title);
  }, [mode, guideSlug, t.seo, locale]);

  if (mode === "about" || mode === "home") {
    return (
      <Landing
        onEnter={() => {
          trackEvent("cta_watch", { from: mode });
          enterWatch();
        }}
        onMacro={() => {
          trackEvent("cta_macro", { from: mode });
          enterMacro();
        }}
        onGuide={() => {
          trackEvent("cta_guide", { from: mode });
          enterGuide();
        }}
        onHome={mode === "about" ? enterHome : undefined}
      />
    );
  }

  const deskActive: DeskTab =
    mode === "macro" || mode === "news" || mode === "guide" ? mode : "watch";

  return (
    <DeskShell
      active={deskActive}
      onWatch={enterWatch}
      onMacro={enterMacro}
      onNews={enterNews}
      onGuide={enterGuide}
      onAbout={enterAbout}
    >
      {mode === "guide" ? (
        <GuideDesk
          slug={guideSlug}
          onWatch={enterWatch}
          onGuideHome={enterGuide}
          onOpenArticle={openGuideArticle}
        />
      ) : mode === "macro" ? (
        <MacroDesk />
      ) : mode === "news" ? (
        <NewsDesk />
      ) : (
        <WatchApp />
      )}
    </DeskShell>
  );
}
