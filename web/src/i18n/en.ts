import type { Messages } from "./types";

export const en: Messages = {
  nav: {
    screens: "Views",
    regime: "Regime",
    macro: "Macro",
    news: "News",
    guide: "Guide",
    about: "About",
    aboutBack: "← About",
    language: "Language",
  },
  common: {
    loading: "Loading…",
    refreshing: "Updating…",
    refresh: "Refresh",
    retry: "Retry",
    error: "Error",
    close: "Close",
    asof: "As of",
    loadFailed: "Could not load data.",
    disclaimer:
      "This information is educational/research material for regime recognition only — not investment advice or a solicitation. You are solely responsible for investment decisions and any losses.",
  },
  watch: {
    confidence: "Confidence",
    agree: "Agreement",
    explainRegime: "Regime guide",
    explainAi: "AI explain",
    regimeModal: "Regime guide",
    analystsModal: "AI analysts",
    volume: "Volume",
    crowd: "Participation",
    actionEdu: "Classic stance for this phase (educational)",
    contextNote:
      "The regime call uses price only. The gauges below are reference context, not inputs to it.",
    measuredHit: "Measured hit rate",
    currentFocus: "Current focus",
    levelHigh: "High",
    levelMid: "Medium",
    levelLow: "Low",
    markets: "Markets",
    marketUs: "US",
    marketCrypto: "Crypto",
    gauges: {
      volume: "Volume",
      participation: "Participation",
      money: "Liquidity",
      sentiment: "Sentiment",
      position: "Position / trend",
    },
    calibrationNote:
      "Walk-forward measure on {window} ({n} bars): exact 6-regime hit rate about {exact} (guessing at random is 1 in 6), calibration error (ECE) {eceLo}–{eceHi}. The trend-rule head is not a trained model; displayed probabilities are pinned to measured hit rates (up/down leg hit {sidePct}%). The three AI conviction scores are rank references, not hit probabilities.",
  },
  frontDoor: {
    title: "Reference context",
    note: "The indicators and headlines below are not inputs to the regime call above. They are background, shown alongside it.",
    newsTitle: "Today’s headlines",
    macroMore: "Full macro desk",
    newsMore: "Full news desk",
  },
  macro: {
    title: "Macro",
    lead: "Rates, jobs, dollar, and mood — then read them on the Kostolany egg. Educational context only.",
    toWatch: "Open regime on the egg",
    hintNote: "Hints map gauges to egg axes (money / sentiment). They are not a live regime call.",
    fedwatch: "Policy tilt (FedWatch proxy)",
    cut: "Cut",
    hold: "Hold",
    hike: "Hike",
    gaugesAria: "Macro gauges",
    briefingAria: "Top news briefing",
    headlines: "Headlines",
    moreNews: "More news",
    hints: {
      rates: "Policy rate · money / liquidity backdrop for the cycle.",
      curve: "Curve shape · credit & growth tone around the egg path.",
      treasury_10y: "Long yields · discount-rate pressure on risk assets.",
      cpi: "Inflation · feeds the money/rates axis over time.",
      breakeven: "Market inflation expectation · forward money pressure.",
      jobs: "Labor · participation & cycle strength context.",
      hy_oas: "Credit stress · risk-off often leans B-side caution.",
      vix: "Equity fear · sentiment axis; spikes lean defensive.",
      dxy: "Dollar strength · global liquidity / risk appetite tone.",
      btc: "Crypto risk asset · mirrors risk-on / risk-off swings.",
      gold: "Haven bid · often rises when fear dominates.",
      fear_greed: "Equity mood proxy · sentiment input, not a call.",
      crypto_fear_greed: "Crypto mood · sentiment input for BTC path.",
    },
  },
  news: {
    title: "News",
    briefingAria: "Today’s focus",
    themes: "Themes",
    all: "All",
    empty: "No headlines",
    official: "Official desks",
    toneGuard: "Caution",
    toneEase: "Ease",
    themeLabels: {
      money: "Money & rates",
      credit: "Credit & risk",
      crypto: "Crypto",
      korea: "Korea market",
      sentiment: "Sentiment & risk appetite",
    },
    toneLabels: {
      easeStrong: "Easing / risk-on",
      easeSoft: "Mild easing bias",
      mixed: "Mixed / neutral",
      guardSoft: "Mild caution bias",
      guardStrong: "Caution / risk-off",
    },
  },
  landing: {
    headline: "See where the market sits on the egg — in probabilities.",
    sub: "Start from macro gauges (rates, jobs, mood), then read Kostolany’s six regimes on the egg — with three AI marks.",
    ctaWatch: "View regime",
    ctaMacro: "Macro gauges",
    whatTitle: "What this is",
    whatBody:
      "Macro context in the door; the egg is the room. We turn volume, participation, liquidity, and sentiment into regime probabilities — not a buy/sell tip, a cycle-reading lens.",
    sixTitle: "Six regimes",
    sixLead: "Upswings climb the right side of the egg; downswings descend the left.",
    analystsTitle: "Three AI analysts",
    finaleTitle: "Start on the egg",
    finaleBody: "One tap takes you straight to the regime view.",
    disclaimer:
      "For education and research on regime recognition only — not investment advice or a solicitation.",
  },
  guide: {
    title: "Guide",
    lead: "Egg & six-regime primers plus weekly briefs. Educational — not investment advice.",
    back: "← All guides",
    cadence: "Evergreen guides plus the live regime view.",
    rss: "RSS",
    missingLocale: "An English version of this article is not available yet.",
  },
  push: {
    title: "Daily regime alerts",
    lead: "Get a browser ping with US & Bitcoin regime metrics at your chosen KST hour. Not a trade signal.",
    hourLabel: "Alert time",
    enable: "Enable alerts",
    disable: "Disable alerts",
    busy: "Working…",
    success: "Alerts on. Keep browser notification permission allowed.",
    off: "Alerts turned off.",
    denied: "Notification permission denied. Allow it in browser settings.",
    unavailable: "Push server is not configured yet.",
    unsupported: "This browser does not support web push.",
    error: "Could not update alerts. Try again shortly.",
    note: "Educational regime snapshots only — not investment advice.",
  },
  seo: {
    home: {
      title: "Macro gauges → Kostolany regimes | Kostolany Watch",
      description:
        "Read rates, jobs, and sentiment — then map them onto André Kostolany’s egg (A1–B3) as educational regime probabilities. US & crypto. Not investment advice.",
      ogTitle: "Macro context, then the egg — where is the cycle?",
      ogDescription:
        "Macro gauges in, Kostolany six-regime lens out. Educational regime reading — not a trading signal.",
    },
    watch: {
      title: "Regime — on the egg",
      description:
        "See S&P 500 and Bitcoin regime probabilities with three AI marks on the egg. Educational regime reading.",
    },
    macro: {
      title: "Macro gauges → egg regimes",
      description:
        "Rates, yields, VIX, dollar, fear & greed — educational gauges that feed Kostolany regime reading. Not investment advice.",
    },
    news: {
      title: "News desk",
      description: "Money, credit, crypto, and sentiment headlines. Not investment advice.",
    },
    about: {
      title: "About",
      description: "What Kostolany Watch does, the six regimes, and three AI analysts.",
    },
    guide: {
      title: "Guide — egg & regimes",
      description: "Kostolany egg primers, six-regime notes, and weekly briefs. Educational.",
    },
  },
  regimes: {
    A1: {
      name: "Correction (up)",
      trait: "Just off the bottom. Little attention, but bad news is largely priced in.",
      volume: "Low volume",
      crowd: "Few participants → slowly rising",
      action: "Accumulate in stages",
    },
    A2: {
      name: "Accompanying (up)",
      trait: "Uptrend takes hold and participation spreads.",
      volume: "Rising volume",
      crowd: "More participants",
      action: "Hold / wait (avoid chase buys)",
    },
    A3: {
      name: "Exaggeration (up)",
      trait: "Overheat and euphoria. Risk rises when everyone is bullish.",
      volume: "Explosive volume",
      crowd: "Maximum participation",
      action: "Scale out / take profits",
    },
    B1: {
      name: "Correction (down)",
      trait: "Post-peak pullback. Optimism lingers while strength fades.",
      volume: "Soft / mixed volume",
      crowd: "Participation starts to drop",
      action: "Trim exposure",
    },
    B2: {
      name: "Accompanying (down)",
      trait: "Decline spreads and sentiment freezes.",
      volume: "Rising volume (capitulation)",
      crowd: "Participants leave",
      action: "Wait / raise cash weight",
    },
    B3: {
      name: "Exaggeration (down)",
      trait: "Fear and forced selling. Opportunity opens when everyone is bearish.",
      volume: "Explosive volume",
      crowd: "Minimum participation",
      action: "Accumulate in stages (bottom fishing)",
    },
  },
  models: {
    momo: {
      label: "Trend Rule",
      short: "TR",
      trait: "No-learning baseline",
      blurb:
        "Majority vote of 8 trend rules plus a turn clock. Not a fitted AI — it measured a higher up/down leg hit rate than all three AI heads, so it is the default. Displayed probability is pinned to measured accuracy.",
    },
    hmm: {
      label: "Rhythm",
      short: "R",
      trait: "Metronome",
      blurb: "Few words, precise beat — a reclusive watchmaker who tracks hidden regime rhythm.",
    },
    gbm: {
      label: "Sharp Eye",
      short: "E",
      trait: "Twig detective",
      blurb: "Quickly reads volume, sentiment, and money flow, then pins it in one sharp line.",
    },
    tsfm: {
      label: "Wave Rider",
      short: "W",
      trait: "Surfer",
      blurb: "Reads the whole chart swell and gauges where the next bend may form.",
    },
  },
  conviction: {
    sideWord: { up: "up", down: "down" },
    badge: {
      unanimous: "8 of 8 trend signals aligned {side} — unanimous",
      strong: "7 of 8 trend signals aligned {side} — strong majority",
      lean: "6 of 8 trend signals aligned {side} — majority",
      mixed: "Signals mixed — split {a} to {b}",
    },
    directionAligned:
      "Direction — of past days with a {tierName} {side} alignment, days that were actually a {side} leg: measured {p}",
    directionMixed:
      "Direction — measured hit rate on split-signal days: {p} · withholding judgement is also information",
    tierName: { unanimous: "unanimous", strong: "strong-majority", lean: "majority" },
    zoneLine:
      "Position — near {regime} · days the true sector was within ±1 of the call: measured {p} (all trading days)",
    tieNote: "A 4-4 tie resolves to the up leg by pre-registered rule",
    detailTitle: "Details",
    ladderDirection: "① Direction (up leg vs down leg): measured {p}",
    ladderZone: "② Zone (call ±1 of 6 sectors): measured {p1} · ±2 (5 of 6) {p2}",
    ladderExact:
      "③ Exact sector (1 of 6): measured {p} · random guess is one in six · structural ceiling {ceiling}",
    ladderExactWhy:
      "Why 'exact sector' is low — sector boundaries are only fixed after the NEXT turning point, so exact-sector accuracy has a structural ceiling. The shown value is the unembellished measurement; low is the honest number.",
    tierTableTitle: "Alignment tiers",
    tierTableCols: { tier: "Alignment", side: "Direction hit (measured)", share: "Frequency" },
    tierTableRows: {
      unanimous: "Unanimous 8-0",
      strong: "Strong 7-1",
      lean: "Majority 6-2",
      mixed: "Mixed 5-3·4-4",
    },
    tierTableShare: "{p} of days",
    tierTableFooter: "{n} trading days · {legs} legs · walk-forward measurement",
    ledgerTitle: "This call is not a black box — all 8 rules disclosed",
    ledgerRules: {
      ma20: "Close > 20-day MA",
      ma40: "Close > 40-day MA",
      ma60: "Close > 60-day MA",
      ma100: "Close > 100-day MA",
      ma200: "Close > 200-day MA",
      ret10: "10-day return > 0",
      ret20: "20-day return > 0",
      ret60: "60-day return > 0",
    },
    ledgerFooter:
      "Majority of 5 moving-average rules + 3 return-sign rules · zero fitted parameters · 4-4 ties resolve up",
    methodTitle: "How were these numbers measured?",
    methodLines: [
      "Every % is a PAST frequency scored against gold labels on a walk-forward out-of-sample window. Not a future probability; no forecast of returns or price direction.",
      "Basis: {n} trading days ({legs} legs) · source file: {source} (kept with the code)",
      "Percentages are always floored, never rounded up.",
      "Alignment tiers track DIRECTION accuracy only. Exact-sector accuracy is flat across tiers, so tiers apply to direction alone.",
      "The headline call is not a fitted model. The maximum displayed probability is pinned to the measured exact-sector hit rate.",
    ],
    eggLegend:
      "Dark arc = today's call range · light band = call ±1 sector · wash = the 3 {sideSectors} sectors, darkness shows alignment tier (not a number)",
    unmeasured: "Hit rates are not shown for unmeasured markets",
    aiRefTitle: "Reference: 3 AI cross-checks — consensus {code} · {k}/{total}",
    aiRefNote:
      "The default call is a majority vote of 8 pre-registered trend rules, not an AI. The AI views (HMM·GBM·TSFM) are for reference.",
  },
  judgment: {
    title: "More grounds for this call",
    summary: {
      heads: "{k} of {n} heads read {side}",
      rules: "{k} of 8 rules point the other way",
      run: "{side} call standing {n} trading days",
      runTruncated: "{side} call standing at least {n} trading days",
    },
    doubt: {
      title: "Reasons to doubt this call",
      rules: "{k} of the 8 rules sit on the other side: {list}",
      rulesNone: "None of the 8 rules sit on the other side.",
      heads: "{k} of {n} heads name a different regime: {list}",
      headsNone: "All {n} heads point at the same leg.",
      flip: "Had today's close been {d} {dir}, this call would have been {regimeTo}.",
      dirDown: "lower",
      dirUp: "higher",
      none:
        "Nothing points the other way right now. An absence of dissent does not mean the call was right — there were unanimous days whose direction was wrong, and the measured tier table above carries that fact unchanged.",
      note:
        "This list is not tied to hit rates — it only shows where the current call is fragile.",
    },
    flip: {
      title: "Where this call flips",
      lead: "Had today's close been …",
      rule: "{d} {dir} “{ruleLabel}” would have gone {side} (alignment {split})",
      ruleNoSplit: "{d} {dir} “{ruleLabel}” would have gone {side}",
      tier: "{d} {dir} the alignment tier would have read “{tierName}”",
      tierMixed: "mixed",
      side:
        "{d} {dir} the 8-rule majority direction itself would have changed, making the call {regimeTo}",
      dirDown: "lower →",
      dirUp: "higher →",
      note1:
        "These values are arithmetic out of the rule definitions. They only show how the same rules would have split with a different close substituted for today's — not a price forecast and not a trading line.",
      note2:
        "The turn clock (early / middle / late) does not read today's close, so it does not move in this calculation.",
    },
    heads: {
      title: "Where the AI heads are looking",
      row: "{label} · {regime} · {side} leg",
      dissentMark: "split",
      agree: "{k} of {n} point at the {side} leg.",
      tied: "The {n} heads split exactly evenly.",
      note:
        "The default call is a majority vote of 8 pre-registered trend rules, not an AI. What is shown here is each head's own call and nothing more — it is not tied to hit rates.",
    },
    run: {
      title: "How old is this call",
      side: "The {side} leg call has stood for {n} trading days (since {since}).",
      sideTruncated:
        "The {side} leg call has stood for at least {n} trading days (back to the start of the shown window).",
      regime: "The {regime} sector call has stood for {n} trading days (since {since}).",
      regimeTruncated:
        "The {regime} sector call has stood for at least {n} trading days (back to the start of the shown window).",
      note1:
        "This is today's rules re-applied to past prices — a recomputation, not a record of what was actually on screen on those days.",
      note2:
        "Direction is settled by the 8 rules alone. The sector split comes from turn-clock terciles, and those terciles were cut over the whole period.",
    },
    cross: {
      title: "The other market",
      row: "{market}: {regime} · trend signals {split} aligned {side}",
      note:
        "The two markets are called separately, each from its own data. Neither one is evidence for the other.",
    },
    archive: {
      title: "What we showed that day",
      row: "{date} — {cells}",
      cell: "{market} {regime} ({split})",
      cellPlain: "{market} {regime}",
      notScored:
        "An unscored record — whether it was right or wrong is not shown. The scoring rules were pinned down separately, before any result was seen.",
      range: "Record starts {first} · {n} days so far",
      empty: "No records to show yet.",
    },
  },
};
