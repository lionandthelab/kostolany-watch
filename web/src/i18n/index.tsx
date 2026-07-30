import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { en } from "./en";
import { ko } from "./ko";
import { LOCALE_META, type EnabledLocale, type LocaleId, type Messages } from "./types";

const STORAGE_KEY = "kostolany.locale";

const CATALOG: Record<EnabledLocale, Messages> = { ko, en };

function isEnabledLocale(v: string): v is EnabledLocale {
  return LOCALE_META.some((m) => m.id === v && m.enabled);
}

function detectLocale(): EnabledLocale {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved && isEnabledLocale(saved)) return saved;
  } catch {
    /* ignore */
  }
  const nav = typeof navigator !== "undefined" ? navigator.language.toLowerCase() : "ko";
  if (nav.startsWith("en")) return "en";
  return "ko";
}

type LocaleCtx = {
  locale: EnabledLocale;
  setLocale: (id: LocaleId) => void;
  t: Messages;
  enabledLocales: typeof LOCALE_META;
  formatDate: (iso?: string | null) => string;
};

const Ctx = createContext<LocaleCtx | null>(null);

export function LocaleProvider({ children }: { children: ReactNode }) {
  const [locale, setLocaleState] = useState<EnabledLocale>(() =>
    typeof window !== "undefined" ? detectLocale() : "ko",
  );

  const setLocale = useCallback((id: LocaleId) => {
    if (!isEnabledLocale(id)) return;
    setLocaleState(id);
    try {
      localStorage.setItem(STORAGE_KEY, id);
    } catch {
      /* ignore */
    }
  }, []);

  useEffect(() => {
    document.documentElement.lang = locale === "ko" ? "ko" : locale;
  }, [locale]);

  const formatDate = useCallback(
    (iso?: string | null) => {
      if (!iso) return "";
      try {
        const tag = locale === "ko" ? "ko-KR" : "en-US";
        return new Date(iso).toLocaleString(tag, {
          timeZone: locale === "ko" ? "Asia/Seoul" : "America/New_York",
        });
      } catch {
        return iso;
      }
    },
    [locale],
  );

  const value = useMemo<LocaleCtx>(
    () => ({
      locale,
      setLocale,
      t: CATALOG[locale],
      enabledLocales: LOCALE_META,
      formatDate,
    }),
    [locale, setLocale, formatDate],
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useLocale() {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useLocale must be used within LocaleProvider");
  return ctx;
}

export function useT() {
  return useLocale().t;
}
