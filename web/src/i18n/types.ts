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
    guide: string;
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
    /** Full educational disclaimer shown under live desks. */
    disclaimer: string;
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
    contextNote: string;
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
      position: string;
    };
    /** Calibration footnote; slots: window, n, exact, chance, eceLo, eceHi, sidePct */
    calibrationNote: string;
  };
  frontDoor: {
    title: string;
    note: string;
    newsTitle: string;
    macroMore: string;
    newsMore: string;
  };
  macro: {
    title: string;
    /** Door → room positioning line under the title */
    lead: string;
    toWatch: string;
    hintNote: string;
    fedwatch: string;
    cut: string;
    hold: string;
    hike: string;
    gaugesAria: string;
    briefingAria: string;
    headlines: string;
    moreNews: string;
    /** Educational egg-axis hints by macro card id (not a regime call). */
    hints: Record<string, string>;
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
    themeLabels: {
      money: string;
      credit: string;
      crypto: string;
      korea: string;
      sentiment: string;
    };
    /** Tone meter labels by score band. */
    toneLabels: {
      easeStrong: string;
      easeSoft: string;
      mixed: string;
      guardSoft: string;
      guardStrong: string;
    };
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
  guide: {
    title: string;
    lead: string;
    back: string;
    cadence: string;
    rss: string;
    missingLocale: string;
  };
  push: {
    title: string;
    lead: string;
    hourLabel: string;
    enable: string;
    disable: string;
    busy: string;
    success: string;
    off: string;
    denied: string;
    unavailable: string;
    unsupported: string;
    error: string;
    note: string;
  };
  seo: {
    home: {
      title: string;
      description: string;
      ogTitle: string;
      ogDescription: string;
    };
    watch: { title: string; description: string };
    macro: { title: string; description: string };
    news: { title: string; description: string };
    about: { title: string; description: string };
    guide: { title: string; description: string };
  };
  regimes: Record<"A1" | "A2" | "A3" | "B1" | "B2" | "B3", RegimeCopy>;
  models: Record<"momo" | "hmm" | "gbm" | "tsfm", ModelCopy>;
  /** Conviction system (spec: research/confidence_spec.md). NO hardcoded
   * percentages — every number arrives through a {p}/{n} slot. */
  conviction: {
    sideWord: { up: string; down: string };
    badge: { unanimous: string; strong: string; lean: string; mixed: string };
    directionAligned: string; // {tierName} {side} {p}
    directionMixed: string; // {p}
    tierName: { unanimous: string; strong: string; lean: string };
    zoneLine: string; // {regime} {p}
    tieNote: string;
    detailTitle: string;
    ladderDirection: string; // {p}
    ladderZone: string; // {p1} {p2}
    ladderExact: string; // {p} {ceiling}
    ladderExactWhy: string;
    tierTableTitle: string;
    tierTableCols: { tier: string; side: string; share: string };
    tierTableRows: { unanimous: string; strong: string; lean: string; mixed: string };
    tierTableShare: string; // {p}
    tierTableFooter: string; // {n} {legs}
    ledgerTitle: string;
    ledgerRules: Record<
      "ma20" | "ma40" | "ma60" | "ma100" | "ma200" | "ret10" | "ret20" | "ret60",
      string
    >;
    ledgerFooter: string;
    methodTitle: string;
    methodLines: string[]; // {n}/{legs}/{source} slots in line 2
    eggLegend: string; // {sideSectors}
    unmeasured: string;
    aiRefTitle: string; // {code} {k} {total}
    aiRefNote: string;
  };
  /** Desk judgment layer (docs/DESK_JUDGMENT_LAYER_2026-08-07.md §6.4).
   * Facts already in the payload — deterministic counts and closed-form price
   * distances. NO hit rate reaches this namespace, so no literal `%` or digit
   * may appear in any string here; every number arrives through a slot.
   * English mirrors stay in the subjunctive past — no future tense. */
  judgment: {
    title: string;
    /** Count-only headline strip — never a percentage (spec §0.4 / §0.7). */
    summary: {
      heads: string; // {n} {k} {side}
      rules: string; // {k}
      run: string; // {side} {n}
      runTruncated: string; // {side} {n}
    };
    doubt: {
      title: string;
      rules: string; // {k} {list}
      rulesNone: string;
      heads: string; // {n} {k} {list}
      headsNone: string; // {n}
      flip: string; // {d} {dir} {regimeTo}
      dirDown: string;
      dirUp: string;
      none: string;
      note: string;
    };
    flip: {
      title: string;
      lead: string;
      rule: string; // {d} {dir} {ruleLabel} {side} {split}
      ruleNoSplit: string; // {d} {dir} {ruleLabel} {side}
      tier: string; // {d} {dir} {tierName}
      tierMixed: string;
      side: string; // {d} {dir} {regimeTo}
      dirDown: string;
      dirUp: string;
      note1: string;
      note2: string;
    };
    heads: {
      title: string;
      row: string; // {label} {regime} {side}
      dissentMark: string;
      agree: string; // {n} {k} {side}
      tied: string; // {n}
      note: string;
    };
    run: {
      title: string;
      side: string; // {side} {n} {since}
      sideTruncated: string; // {side} {n}
      regime: string; // {regime} {n} {since}
      regimeTruncated: string; // {regime} {n}
      note1: string;
      note2: string;
    };
    cross: {
      title: string;
      row: string; // {market} {regime} {split} {side}
      note: string;
    };
    archive: {
      title: string;
      row: string; // {date} {cells}
      cell: string; // {market} {regime} {split}
      cellPlain: string; // {market} {regime}
      notScored: string;
      range: string; // {first} {n}
      empty: string;
    };
  };
};
