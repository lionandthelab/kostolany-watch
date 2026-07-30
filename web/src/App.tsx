import { useCallback, useEffect, useState } from "react";
import MacroDesk from "./MacroDesk";
import Landing from "./Landing";
import NewsDesk from "./NewsDesk";
import WatchApp from "./WatchApp";
import { trackEvent, trackPageView } from "./analytics";
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
};

function modeFromPath(): Mode {
  const path = window.location.pathname.replace(/\/+$/, "") || "/";
  if (path === "/watch") return "watch";
  if (path === "/macro") return "macro";
  if (path === "/news") return "news";
  if (path === "/about") return "about";
  return "home";
}

function pathFor(mode: Mode): string {
  if (mode === "watch") return "/watch";
  if (mode === "macro") return "/macro";
  if (mode === "news") return "/news";
  if (mode === "about") return "/about";
  return "/";
}

/** Old hash bookmarks → path routes (SEO-friendly). */
function migrateHashIfNeeded() {
  const h = window.location.hash;
  if (!h) return;
  const next = HASH_MAP[h];
  if (next) {
    window.history.replaceState(null, "", next);
  }
}

export default function App() {
  const t = useT();
  const { locale } = useLocale();
  const [mode, setMode] = useState<Mode>(() => {
    if (typeof window === "undefined") return "home";
    migrateHashIfNeeded();
    return modeFromPath();
  });

  const go = useCallback((next: Mode, source = "nav") => {
    setMode(next);
    const path = pathFor(next);
    if (window.location.pathname !== path) {
      window.history.pushState(null, "", path);
    }
    window.scrollTo(0, 0);
    trackEvent("navigate", { screen: next, source });
  }, []);

  const enterWatch = useCallback(() => go("watch", "cta_or_nav"), [go]);
  const enterMacro = useCallback(() => go("macro", "nav"), [go]);
  const enterNews = useCallback(() => go("news", "nav"), [go]);
  const enterAbout = useCallback(() => go("about", "nav"), [go]);
  const enterHome = useCallback(() => go("home", "nav"), [go]);

  useEffect(() => {
    migrateHashIfNeeded();
    const onPop = () => setMode(modeFromPath());
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  useEffect(() => {
    const key = mode === "home" ? "home" : mode;
    applySeo(t.seo[key], pathFor(mode), locale);
    trackPageView(pathFor(mode), document.title);
  }, [mode, t.seo, locale]);

  if (mode === "macro") {
    return <MacroDesk onWatch={enterWatch} onAbout={enterAbout} onNews={enterNews} />;
  }
  if (mode === "news") {
    return (
      <NewsDesk onWatch={enterWatch} onMacro={enterMacro} onAbout={enterAbout} />
    );
  }
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
        onHome={mode === "about" ? enterHome : undefined}
      />
    );
  }
  return (
    <WatchApp
      onMacro={enterMacro}
      onAbout={enterAbout}
      onNews={enterNews}
    />
  );
}
