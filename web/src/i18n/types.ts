/** Planned locales: ko/en live now; ja/zh/es reserved. */
export const LOCALE_META = [
  { id: "ko", nativeLabel: "한국어", enabled: true },
  { id: "en", nativeLabel: "English", enabled: true },
  { id: "ja", nativeLabel: "日本語", enabled: false },
  { id: "zh", nativeLabel: "中文", enabled: false },
  { id: "es", nativeLabel: "Español", enabled: false },
] as const;

export type LocaleId = (typeof LOCALE_META)[number]["id"];
export type EnabledLocale = Extract<(typeof LOCALE_META)[number], { enabled: true }>["id"];

export type RegimeCopy = {
  name: string;
  trait: string;
  volume: string;
  crowd: string;
  action: string;
};

export type ModelCopy = {
  label: string;
  short: string;
  trait: string;
  blurb: string;
};

export type Messages = {
  nav: {
    screens: string;
    regime: string;
    macro: string;
    news: string;
    about: string;
    aboutBack: string;
    language: string;
  };
  common: {
    loading: string;
    refreshing: string;
    refresh: string;
    retry: string;
    error: string;
    close: string;
    asof: string;
    loadFailed: string;
  };
  watch: {
    confidence: string;
    agree: string;
    explainRegime: string;
    explainAi: string;
    regimeModal: string;
    analystsModal: string;
    volume: string;
    crowd: string;
    actionEdu: string;
    measuredHit: string;
    currentFocus: string;
    levelHigh: string;
    levelMid: string;
    levelLow: string;
    markets: string;
    marketUs: string;
    marketCrypto: string;
    gauges: {
      volume: string;
      participation: string;
      money: string;
      sentiment: string;
    };
  };
  macro: {
    title: string;
    fedwatch: string;
    cut: string;
    hold: string;
    hike: string;
    gaugesAria: string;
    briefingAria: string;
    headlines: string;
    moreNews: string;
  };
  news: {
    title: string;
    briefingAria: string;
    themes: string;
    all: string;
    empty: string;
    official: string;
    toneGuard: string;
    toneEase: string;
  };
  landing: {
    headline: string;
    sub: string;
    ctaWatch: string;
    ctaMacro: string;
    whatTitle: string;
    whatBody: string;
    sixTitle: string;
    sixLead: string;
    analystsTitle: string;
    finaleTitle: string;
    finaleBody: string;
    disclaimer: string;
  };
  seo: {
    home: { title: string; description: string };
    watch: { title: string; description: string };
    macro: { title: string; description: string };
    news: { title: string; description: string };
    about: { title: string; description: string };
  };
  regimes: Record<"A1" | "A2" | "A3" | "B1" | "B2" | "B3", RegimeCopy>;
  models: Record<"hmm" | "gbm" | "tsfm", ModelCopy>;
};
