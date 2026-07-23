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
};

const API = "/api";

async function fetchJson<T>(url: string, retries = 3): Promise<T> {
  let lastErr: Error | null = null;
  for (let attempt = 0; attempt < retries; attempt++) {
    const res = await fetch(url);
    if (res.status === 429 || res.status === 503) {
      lastErr = new Error(`서버가 바빠요 (${res.status}). 잠시 후 다시 시도합니다…`);
      await new Promise((r) => setTimeout(r, 800 * (attempt + 1) ** 2));
      continue;
    }
    if (!res.ok) throw new Error(await res.text());
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

/** Single request for all analysts — preferred on Hosting → Cloud Run. */
export async function fetchWatch(
  symbol: string,
  models: string[] = ["hmm", "gbm", "tsfm"],
  limit = 360,
): Promise<WatchBundle> {
  const q = new URLSearchParams({
    symbol,
    models: models.join(","),
    limit: String(limit),
    stride: "2",
  });
  return fetchJson(`${API}/watch?${q}`, 4);
}
