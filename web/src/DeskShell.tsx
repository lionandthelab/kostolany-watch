import type { ReactNode } from "react";
import AdSlot from "./AdSlot";
import LocaleSwitcher from "./LocaleSwitcher";
import { useT } from "./i18n";

export type DeskTab = "watch" | "macro" | "news" | "guide";

type Props = {
  active: DeskTab;
  onWatch: () => void;
  onMacro: () => void;
  onNews: () => void;
  onGuide: () => void;
  onAbout?: () => void;
  children: ReactNode;
};

/** Shared sticky desk chrome — tabs stay put; only the body swaps. */
export default function DeskShell({
  active,
  onWatch,
  onMacro,
  onNews,
  onGuide,
  onAbout,
  children,
}: Props) {
  const t = useT();
  return (
    <div className="desk-shell">
      <header className="desk-chrome">
        <nav className="desk-nav desk-rail" aria-label={t.nav.screens}>
          <div className="desk-tabs" role="tablist">
            <button
              type="button"
              role="tab"
              className={`desk-tab${active === "watch" ? " is-active" : ""}`}
              aria-selected={active === "watch"}
              aria-current={active === "watch" ? "page" : undefined}
              onClick={onWatch}
            >
              {t.nav.regime}
            </button>
            <button
              type="button"
              role="tab"
              className={`desk-tab${active === "macro" ? " is-active" : ""}`}
              aria-selected={active === "macro"}
              aria-current={active === "macro" ? "page" : undefined}
              onClick={onMacro}
            >
              {t.nav.macro}
            </button>
            <button
              type="button"
              role="tab"
              className={`desk-tab${active === "news" ? " is-active" : ""}`}
              aria-selected={active === "news"}
              aria-current={active === "news" ? "page" : undefined}
              onClick={onNews}
            >
              {t.nav.news}
            </button>
            <button
              type="button"
              role="tab"
              className={`desk-tab${active === "guide" ? " is-active" : ""}`}
              aria-selected={active === "guide"}
              aria-current={active === "guide" ? "page" : undefined}
              onClick={onGuide}
            >
              {t.nav.guide}
            </button>
          </div>
          <div className="desk-nav-end">
            <LocaleSwitcher />
            {onAbout && (
              <button type="button" className="nav-quiet nav-btn" onClick={onAbout}>
                {t.nav.about}
              </button>
            )}
          </div>
        </nav>
      </header>
      <div className="desk-body desk-rail">
        {children}
        <AdSlot />
      </div>
    </div>
  );
}
