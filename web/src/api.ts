export type EvidenceItem = {
  key: string;
  label: string;
  value: number;
  level: string;
  detail: string;
};

export type Snapshot = {
  symbol: string;
  asof: string;
  regime: string;
  regime_name_ko: string;
  confidence: number;
  probabilities: Record<string, number>;
  gauges: Record<string, number>;
  egg: { x: number; y: number };
  action_ko: string;
  next_likely: Array<{
    from: string;
    to: string;
    to_name_ko: string;
    proximity: number;
    note: string;
    tsfm_transition?: number;
  }>;
  disclaimer: string;
  transition_score?: number | null;
  evidence?: EvidenceItem[] | null;
  vote?: VoteBlock | null;
};

export type RegimeInfo = {
  code: string;
  name_ko: string;
  name_en: string;
  action_ko: string;
  color: string;
  egg_x: number;
  egg_y: number;
};

export type ReplayFrame = {
  date: string;
  regime: string;
  regime_name_ko: string;
  confidence: number;
  probabilities: Record<string, number>;
  gauges: Record<string, number>;
  egg: { x: number; y: number };
  action_ko: string;
  close: number | null;
};

export type ReplayResponse = {
  symbol: string;
  n: number;
  frames: ReplayFrame[];
  disclaimer: string;
};

export type WatchAnalyst = {
  id: string;
  snapshot: Snapshot;
  replay: ReplayResponse;
};

/** Live 8-rule vote block — momo head only; null on AI heads / fail-closed. */
export type VoteBlock = {
  up: number;
  down: number;
  split: string;
  tier: "unanimous" | "strong" | "lean" | "mixed";
  side: "up" | "down";
  rules: { id: string; vote: "up" | "down" }[];
};

/** Frozen measured conditional tables backing the conviction display. */
export type ConfidenceView = {
  source: string;
  measured_at: string;
  n_bars: number;
  n_legs: number;
  rounding: string;
  menu: {
    side_hit: number;
    zone1_hit: number;
    zone2_hit: number;
    exact_hit: number;
    third_given_side: number;
    exact_ceiling: number;
  };
  tiers: Record<
    "unanimous" | "strong" | "lean" | "mixed",
    { split: string; side_hit: number; zone1_hit: number; exact_hit: number; share: number }
  >;
};

/** Measured out-of-sample calibration facts — not a model output. */
export type RegimeCalibration = {
  confidence_view?: ConfidenceView;
  measured: Record<string, { exact6: number; adjacent: number; ece: number; brier: number }>;
  constant_prior_baseline: { exact6: number; adjacent: number; ece: number; brier: number };
  exact6_chance: number;
  adjacent_floor: number;
  n_oos_bars: number;
  n_oos_legs: number;
  window: string;
  artifact: string;
  confidence_is_calibrated: boolean;
  note_ko: string;
};

export type WatchBundle = {
  symbol: string;
  analysts: WatchAnalyst[];
  disclaimer: string;
  calibration?: RegimeCalibration;
  cached?: boolean;
  stale?: boolean;
  refreshing?: boolean;
  cached_at?: string | null;
  expires_at?: string | null;
  can_refresh?: boolean;
  refresh_available_at?: string | null;
};

const API = "/api";

/**
 * Global API gate:
 * - serialize peeks
 * - after Cloud Run 429/503, open a circuit so we do NOT fetch again
 *   (avoids red DevTools spam; callers see synthetic "busy")
 */
let _peekTail: Promise<unknown> = Promise.resolve();
let _lastPeekAt = 0;
const PEEK_GAP_MS = 1200;
let _circuitOpenUntil = 0;
let _circuitBackoffMs = 8000;

function circuitOpen(): boolean {
  return Date.now() < _circuitOpenUntil;
}

function tripCircuit(): void {
  _circuitOpenUntil = Date.now() + _circuitBackoffMs;
  _circuitBackoffMs = Math.min(60000, Math.round(_circuitBackoffMs * 1.6));
}

function easeCircuit(): void {
  _circuitBackoffMs = 8000;
}

function syntheticBusy(): Response {
  return new Response(null, { status: 503, statusText: "circuit-open" });
}

async function throttledFetch(url: string, init?: RequestInit): Promise<Response> {
  let result!: Response;
  _peekTail = _peekTail.then(async () => {
    if (circuitOpen()) {
      result = syntheticBusy();
      return;
    }
    const gap = Math.max(0, PEEK_GAP_MS - (Date.now() - _lastPeekAt));
    if (gap) await new Promise((r) => setTimeout(r, gap));
    if (circuitOpen()) {
      result = syntheticBusy();
      return;
    }
    _lastPeekAt = Date.now();
    result = await fetch(url, init);
    if (result.status === 429 || result.status === 503) {
      tripCircuit();
    } else if (result.ok || result.status === 204 || result.status === 202) {
      easeCircuit();
    }
  });
  await _peekTail;
  return result;
}

/** Cache peeks always hit the network — never blocked by the POST/warmup circuit. */
async function cachePeekFetch(url: string): Promise<Response> {
  let result!: Response;
  _peekTail = _peekTail.then(async () => {
    const gap = Math.max(0, Math.min(400, PEEK_GAP_MS - (Date.now() - _lastPeekAt)));
    if (gap) await new Promise((r) => setTimeout(r, gap));
    _lastPeekAt = Date.now();
    try {
      result = await fetch(url);
      if (result.status === 429 || result.status === 503) {
        tripCircuit();
      } else if (result.ok || result.status === 204 || result.status === 202) {
        easeCircuit();
      }
    } catch {
      result = syntheticBusy();
    }
  });
  await _peekTail;
  return result;
}

async function parseError(res: Response): Promise<string> {
  const text = await res.text();
  try {
    const j = JSON.parse(text) as { detail?: string };
    if (j.detail) return j.detail;
  } catch {
    /* keep text */
  }
  return text || `HTTP ${res.status}`;
}

async function fetchJson<T>(url: string, retries = 3, init?: RequestInit): Promise<T> {
  let lastErr: Error | null = null;
  for (let attempt = 0; attempt < retries; attempt++) {
    if (circuitOpen()) {
      const wait = Math.max(500, _circuitOpenUntil - Date.now());
      await new Promise((r) => setTimeout(r, wait));
      if (circuitOpen() && attempt === retries - 1) {
        throw new Error("서버가 바빠요. 잠시 후 다시 시도해 주세요.");
      }
      continue;
    }
    const res = await fetch(url, init);
    if (res.status === 429 || res.status === 503) {
      const detail = await parseError(res);
      if (detail.includes("리프레시") || detail.includes("1시간")) {
        throw new Error(detail);
      }
      tripCircuit();
      lastErr = new Error(`서버가 바빠요. 잠시 후 다시 시도해 주세요.`);
      await new Promise((r) => setTimeout(r, Math.max(1000, _circuitOpenUntil - Date.now())));
      continue;
    }
    if (!res.ok) throw new Error(await parseError(res));
    easeCircuit();
    return res.json() as Promise<T>;
  }
  throw lastErr ?? new Error("요청 실패");
}

export async function fetchSnapshot(symbol: string, model: string, asof?: string): Promise<Snapshot> {
  const q = new URLSearchParams({ symbol, model });
  if (asof) q.set("asof", asof);
  return fetchJson(`${API}/snapshot?${q}`);
}

export async function fetchRegimes(): Promise<RegimeInfo[]> {
  return fetchJson(`${API}/regimes`);
}

export async function fetchReplay(symbol: string, model: string, limit = 360): Promise<ReplayResponse> {
  const q = new URLSearchParams({ symbol, model, limit: String(limit), stride: "2" });
  return fetchJson(`${API}/replay?${q}`);
}

export type PeekResult<T> =
  | { status: "hit"; data: T }
  | { status: "miss" }
  | { status: "busy" };

export async function peekWatch(
  symbol: string,
  models: string[] = ["hmm", "gbm", "tsfm"],
  limit = 360,
): Promise<PeekResult<WatchBundle>> {
  const q = new URLSearchParams({
    symbol,
    models: models.join(","),
    limit: String(limit),
    stride: "2",
    peek: "true",
  });
  const res = await cachePeekFetch(`${API}/watch?${q}`);
  if (res.status === 204) return { status: "miss" };
  if (res.status === 429 || res.status === 503) return { status: "busy" };
  if (!res.ok) throw new Error(await parseError(res));
  return { status: "hit", data: (await res.json()) as WatchBundle };
}

export async function fetchWatch(
  symbol: string,
  models: string[] = ["hmm", "gbm", "tsfm"],
  limit = 360,
  refresh = false,
): Promise<WatchBundle> {
  const q = new URLSearchParams({
    symbol,
    models: models.join(","),
    limit: String(limit),
    stride: "2",
  });
  if (refresh) q.set("refresh", "true");
  return fetchJson(`${API}/watch?${q}`, refresh ? 1 : 3);
}

export async function startWatchWarmup(force = false): Promise<{ running: boolean; cached: string[] } | null> {
  // Once per tab session — never stampede Cloud Run with warmup POSTs
  const key = `kw_watch_warmup_${force ? "f" : "n"}`;
  if (typeof sessionStorage !== "undefined" && sessionStorage.getItem(key) && !force) {
    return null;
  }
  if (circuitOpen()) return null;
  try {
    if (typeof sessionStorage !== "undefined") sessionStorage.setItem(key, "1");
  } catch {
    /* private mode */
  }
  const q = force ? "?force=true" : "";
  try {
    const ctrl = new AbortController();
    const t = window.setTimeout(() => ctrl.abort(), 4000);
    const res = await fetch(`${API}/watch/warmup${q}`, { method: "POST", signal: ctrl.signal });
    window.clearTimeout(t);
    if (res.status === 429 || res.status === 503 || res.status === 502) {
      tripCircuit();
      return null;
    }
    if (!res.ok) return null;
    easeCircuit();
    return (await res.json()) as { running: boolean; cached: string[] };
  } catch {
    return null;
  }
}

export async function ensureWatchMarket(symbol: string, force = false): Promise<void> {
  if (circuitOpen()) return;
  const q = new URLSearchParams({ symbol });
  if (force) q.set("force", "true");
  try {
    const res = await throttledFetch(`${API}/watch/ensure?${q}`, { method: "POST" });
    if (res.status === 429 || res.status === 503 || res.status === 502) return;
  } catch {
    /* peek poll will retry */
  }
}

export async function fetchWatchOne(
  symbol: string,
  model: string,
  limit = 360,
): Promise<WatchAnalyst> {
  const q = new URLSearchParams({
    symbol,
    model,
    limit: String(limit),
    stride: "2",
  });
  const data = await fetchJson<{ analyst: WatchAnalyst }>(`${API}/watch/one?${q}`, 3);
  return data.analyst;
}

export async function beginWatchRefresh(
  symbol: string,
  models: string[] = ["hmm", "gbm", "tsfm"],
  limit = 360,
): Promise<{ can_refresh: boolean; refresh_available_at?: string | null }> {
  const q = new URLSearchParams({
    symbol,
    models: models.join(","),
    limit: String(limit),
    stride: "2",
  });
  return fetchJson(`${API}/watch/begin-refresh?${q}`, 1, { method: "POST" });
}

export async function sealWatch(
  symbol: string,
  models: string[] = ["hmm", "gbm", "tsfm"],
  limit = 360,
  refreshed = false,
): Promise<WatchBundle> {
  const q = new URLSearchParams({
    symbol,
    models: models.join(","),
    limit: String(limit),
    stride: "2",
    refreshed: refreshed ? "true" : "false",
  });
  return fetchJson(`${API}/watch/seal?${q}`, 2, { method: "POST" });
}

export async function fetchNews(refresh = false): Promise<NewsDesk> {
  const q = refresh ? "?refresh=true" : "";
  return fetchJson(`${API}/news${q}`, refresh ? 1 : 3);
}

export type FlowPoint = { date: string; value: number };

export type MacroCard = {
  id: string;
  title: string;
  value: number | null;
  unit: string;
  delta?: number | null;
  delta_label?: string;
  series: FlowPoint[];
  blurb?: string;
  extra?: Record<string, unknown>;
};

export type MacroBoard = {
  asof?: string;
  source?: string;
  cards: MacroCard[];
  treasury_10y?: number | null;
  fedwatch?: {
    label?: string;
    cut?: number | null;
    hold?: number | null;
    hike?: number | null;
    gap_pp?: number;
    note?: string;
    source?: string;
  };
  fear_greed?: {
    score?: number;
    label?: string;
    series?: FlowPoint[];
    disclaimer?: string;
  };
  disclaimer: string;
  cached?: boolean;
  error?: string;
};

export async function fetchMacroBoard(refresh = false): Promise<MacroBoard> {
  const q = refresh ? "?refresh=true" : "";
  return fetchJson(`${API}/macro${q}`, refresh ? 1 : 3);
}

/** Terminal q10/q90 as 100-based index levels, consistent with `points`. */
export type FlowBand = { q10: number; q90: number };

/** "regime_prior" = closed-form scenario over hardcoded drifts, not a forecast. */
export type FlowArmKind = "regime_prior" | "learned";

export type FlowForecast = {
  id: string;
  label: string;
  color: string;
  regime: string;
  confidence: number;
  outlook: "up" | "down" | "flat";
  change_pct: number;
  points: FlowPoint[];
  /** pooled_v1 | local_tsfm | regime_prior | regime_prior_fallback */
  engine?: string;
  arm_kind?: FlowArmKind;
  /** null whenever the model produced no quantiles — never synthesised. */
  band?: FlowBand | null;
  /** Calibrated P(63d return > 0); null when the arm has no direction head. */
  p_up?: number | null;
};

export type HistRange = "6m" | "1y" | "3y" | "5y";

export type SectorFlow = {
  sector: { id: string; label: string; symbol: string; blurb: string };
  asof: string;
  cached?: boolean;
  stale?: boolean;
  refreshing?: boolean;
  built_at?: string;
  history: FlowPoint[];
  hist_range?: HistRange | string;
  hist_range_label?: string;
  forecasts: FlowForecast[];
  consensus: { change_pct: number; outlook: "up" | "down" | "flat" } | null;
  forecast_pending?: boolean;
  /** Engine backing the one learnable arm on this payload. */
  forecast_engine?: string;
  /** Unconditional share of positive 63-bar forward returns (0..1). */
  base_rate_up?: number | null;
  /** Overlapping bar count behind `base_rate_up` — not independent samples. */
  base_rate_n?: number;
  disclaimer: string;
};

export type SectorInfo = { id: string; label: string; blurb: string; symbol?: string };

export type SectorGroup = { id: string; label_ko: string; sector_ids: string[] };

export type FlowCatalog = { sectors: SectorInfo[]; groups: SectorGroup[] };

export type FlowWarmupStatus = {
  running: boolean;
  done: number;
  total: number;
  current: string | null;
  cached_sectors: string[];
  refreshing?: string[];
};

export async function fetchFlowCatalog(): Promise<FlowCatalog> {
  const data = await fetchJson<FlowCatalog>(`${API}/flows/sectors`);
  return {
    sectors: data.sectors ?? [],
    groups: data.groups ?? [],
  };
}

/** @deprecated use fetchFlowCatalog */
export async function fetchFlowSectors(): Promise<SectorInfo[]> {
  const cat = await fetchFlowCatalog();
  return cat.sectors;
}

export async function peekSectorFlow(sector: string): Promise<PeekResult<SectorFlow>> {
  const q = new URLSearchParams({ sector, peek: "true" });
  const res = await cachePeekFetch(`${API}/flows?${q}`);
  if (res.status === 204) return { status: "miss" };
  if (res.status === 429 || res.status === 503) return { status: "busy" };
  if (!res.ok) return { status: "miss" };
  return { status: "hit", data: (await res.json()) as SectorFlow };
}

/** Fast OHLCV chart (no AI). Safe to call while forecasts build. */
export async function fetchSectorHistory(
  sector: string,
  refresh = false,
  range: HistRange = "1y",
): Promise<SectorFlow> {
  const q = new URLSearchParams({ sector, range });
  if (refresh) q.set("refresh", "true");
  return fetchJson(`${API}/flows/history?${q}`, refresh ? 1 : 3);
}

/** Non-blocking: returns null on 202 (building) / 204. Never asks the API to wait/sync. */
export async function fetchSectorFlow(sector: string, refresh = false): Promise<SectorFlow | null> {
  const q = new URLSearchParams({ sector, wait: "false" });
  if (refresh) q.set("refresh", "true");
  // Prefer cache path even when circuit is open (SWR payload is cheap).
  const res = refresh ? await throttledFetch(`${API}/flows?${q}`) : await cachePeekFetch(`${API}/flows?${q}`);
  if (res.status === 202 || res.status === 204 || res.status === 429 || res.status === 503) {
    return null;
  }
  if (!res.ok) throw new Error(await parseError(res));
  return (await res.json()) as SectorFlow;
}

export async function ensureSectorFlow(sector: string, force = false): Promise<void> {
  const q = new URLSearchParams({ sector });
  if (force) q.set("force", "true");
  try {
    const ctrl = new AbortController();
    const t = window.setTimeout(() => ctrl.abort(), 4000);
    const res = await fetch(`${API}/flows/ensure?${q}`, { method: "POST", signal: ctrl.signal });
    window.clearTimeout(t);
    if (res.status === 429 || res.status === 503) tripCircuit();
  } catch {
    /* peek poll will retry */
  }
}

export async function startFlowsWarmup(force = false): Promise<FlowWarmupStatus | null> {
  const key = `kw_flows_warmup_${force ? "f" : "n"}`;
  if (typeof sessionStorage !== "undefined" && sessionStorage.getItem(key) && !force) {
    return null;
  }
  if (circuitOpen() && !force) return null;
  try {
    if (typeof sessionStorage !== "undefined") sessionStorage.setItem(key, "1");
  } catch {
    /* private mode */
  }
  const q = force ? "?force=true" : "";
  try {
    const ctrl = new AbortController();
    const t = window.setTimeout(() => ctrl.abort(), 4000);
    const res = await fetch(`${API}/flows/warmup${q}`, { method: "POST", signal: ctrl.signal });
    window.clearTimeout(t);
    if (res.status === 429 || res.status === 503 || res.status === 502) {
      tripCircuit();
      return null;
    }
    if (!res.ok) return null;
    easeCircuit();
    return (await res.json()) as FlowWarmupStatus;
  } catch {
    return null;
  }
}

export async function fetchFlowsWarmupStatus(): Promise<FlowWarmupStatus> {
  return fetchJson(`${API}/flows/warmup`);
}

export type FearGreedGauge = {
  score: number;
  label: string;
  asof?: string;
  source?: string;
  disclaimer?: string;
  series?: FlowPoint[];
  components?: {
    vix?: number | null;
    spy_ret_20?: number | null;
    irx_chg_20_bp?: number | null;
  };
};

export async function fetchFearGreed(refresh = false): Promise<FearGreedGauge> {
  const q = refresh ? "?refresh=true" : "";
  return fetchJson(`${API}/flows/gauge${q}`, refresh ? 1 : 3);
}

export type NewsletterSubscribeResult = {
  ok: boolean;
  status: string;
  disclaimer?: string;
  error?: string;
};

export async function subscribeNewsletter(
  email: string,
  opts: { locale?: string; source?: string; website?: string } = {},
): Promise<NewsletterSubscribeResult> {
  const res = await fetch(`${API}/newsletter/subscribe`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      email,
      locale: opts.locale ?? "ko",
      source: opts.source ?? "web",
      website: opts.website ?? "",
    }),
  });
  const data = (await res.json().catch(() => ({}))) as NewsletterSubscribeResult & {
    detail?: string;
  };
  if (!res.ok) {
    const detail = data.detail || data.error || String(res.status);
    throw new Error(detail);
  }
  return data;
}

export type RemoteBrief = {
  slug: string;
  kind: "weekly" | "daily" | string;
  date: string;
  title: { ko: string; en: string };
  description?: { ko: string; en: string };
  body?: { ko: string; en: string };
};

export async function fetchBriefs(limit = 40): Promise<RemoteBrief[]> {
  const res = await fetch(`${API}/briefs?limit=${limit}`);
  if (!res.ok) return [];
  const data = (await res.json()) as { items?: RemoteBrief[] };
  return data.items || [];
}

export async function fetchBrief(slug: string): Promise<RemoteBrief | null> {
  const res = await fetch(`${API}/briefs/${encodeURIComponent(slug)}`);
  if (res.status === 404) return null;
  if (!res.ok) return null;
  return (await res.json()) as RemoteBrief;
}

export type NewsTone = {
  score: number;
  label: string;
  bull: number;
  bear: number;
  n: number;
};

export type NewsItem = {
  id: string;
  title: string;
  url: string;
  summary: string;
  source: string;
  theme: string;
  theme_ko: string;
  why: string;
  published_at?: string | null;
};

export type NewsSection = {
  theme: string;
  label_ko: string;
  why: string;
  summary_ko?: string;
  tone?: NewsTone;
  items: NewsItem[];
};

export type NewsDesk = {
  asof?: string;
  cached?: boolean;
  stale?: boolean;
  refreshing?: boolean;
  ttl_hours?: number;
  desk_links: Array<{ title: string; url: string; theme: string; source: string }>;
  sections: NewsSection[];
  items: NewsItem[];
  priority_summary_md?: string;
  disclaimer: string;
};
