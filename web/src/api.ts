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

export type WatchBundle = {
  symbol: string;
  analysts: WatchAnalyst[];
  disclaimer: string;
  cached?: boolean;
  stale?: boolean;
  refreshing?: boolean;
  cached_at?: string | null;
  expires_at?: string | null;
  can_refresh?: boolean;
  refresh_available_at?: string | null;
};

const API = "/api";

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
    const res = await fetch(url, init);
    if (res.status === 429) {
      const detail = await parseError(res);
      if (detail.includes("리프레시") || detail.includes("1시간")) {
        throw new Error(detail);
      }
      lastErr = new Error(`서버가 바빠요 (429). 잠시 후 다시 시도합니다…`);
      await new Promise((r) => setTimeout(r, 800 * (attempt + 1) ** 2));
      continue;
    }
    if (res.status === 503) {
      lastErr = new Error(`서버가 바빠요 (503). 잠시 후 다시 시도합니다…`);
      await new Promise((r) => setTimeout(r, 800 * (attempt + 1) ** 2));
      continue;
    }
    if (!res.ok) throw new Error(await parseError(res));
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
  const res = await fetch(`${API}/watch?${q}`);
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

export async function startWatchWarmup(force = false): Promise<{ running: boolean; cached: string[] }> {
  const q = force ? "?force=true" : "";
  return fetchJson(`${API}/watch/warmup${q}`, 1, { method: "POST" });
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

export type FlowForecast = {
  id: string;
  label: string;
  color: string;
  regime: string;
  confidence: number;
  outlook: "up" | "down";
  change_pct: number;
  points: FlowPoint[];
};

export type SectorFlow = {
  sector: { id: string; label: string; symbol: string; blurb: string };
  asof: string;
  cached?: boolean;
  stale?: boolean;
  refreshing?: boolean;
  built_at?: string;
  history: FlowPoint[];
  forecasts: FlowForecast[];
  consensus: { change_pct: number; outlook: "up" | "down" };
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
  const res = await fetch(`${API}/flows?${q}`);
  if (res.status === 204) return { status: "miss" };
  if (res.status === 429 || res.status === 503) return { status: "busy" };
  if (!res.ok) return { status: "miss" };
  return { status: "hit", data: (await res.json()) as SectorFlow };
}

export async function fetchSectorFlow(sector: string, refresh = false): Promise<SectorFlow> {
  const q = new URLSearchParams({ sector });
  if (refresh) q.set("refresh", "true");
  return fetchJson(`${API}/flows?${q}`, refresh ? 1 : 3);
}

export async function startFlowsWarmup(force = false): Promise<FlowWarmupStatus> {
  const q = force ? "?force=true" : "";
  return fetchJson(`${API}/flows/warmup${q}`, 1, { method: "POST" });
}

export async function fetchFlowsWarmupStatus(): Promise<FlowWarmupStatus> {
  return fetchJson(`${API}/flows/warmup`);
}

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
