/**
 * Client for the read-only research API.
 *
 * Every response from this API is research output that may be missing,
 * stale, or based on poor-quality data. The UI must render those states
 * explicitly rather than showing a plausible-looking blank.
 */

const BASE = import.meta.env.VITE_API_BASE || "http://127.0.0.1:8000";

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

async function get(path, params) {
  const url = new URL(BASE + path);
  Object.entries(params || {}).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== "") {
      url.searchParams.set(key, value);
    }
  });
  let response;
  try {
    response = await fetch(url, { headers: { Accept: "application/json" } });
  } catch (cause) {
    throw new ApiError(
      `Cannot reach the research API at ${BASE}. Start it with ` +
        `\`research-engine serve\`.`,
      0,
    );
  }
  if (!response.ok) {
    let detail = response.statusText;
    try {
      detail = (await response.json()).detail || detail;
    } catch {
      /* keep the status text */
    }
    throw new ApiError(detail, response.status);
  }
  return response.json();
}

export const api = {
  base: BASE,
  health: () => get("/api/health"),
  market: () => get("/api/market"),
  opportunities: (params) => get("/api/opportunities", params),
  screen: (params) => get("/api/screen", params),
  assets: (params) => get("/api/assets", params),
  asset: (symbol) => get(`/api/asset/${encodeURIComponent(symbol)}`),
  prices: (symbol, days) =>
    get(`/api/asset/${encodeURIComponent(symbol)}/prices`, { days }),
  memo: (symbol) => get(`/api/asset/${encodeURIComponent(symbol)}/memo`),
  portfolio: () => get("/api/portfolio"),
  performance: (modelVersion) =>
    get("/api/performance", { model_version: modelVersion }),
  queue: (limit) => get("/api/queue", { limit }),
  alerts: (params) => get("/api/alerts", params),
  report: (kind) => get("/api/report", { kind }),
  providers: () => get("/api/providers"),
};

/** Formatting helpers that render missing values honestly. */
export const fmt = {
  pct(value, digits = 1) {
    if (value === null || value === undefined || Number.isNaN(value)) return "n/a";
    return `${(value * 100).toFixed(digits)}%`;
  },
  money(value, digits = 2) {
    if (value === null || value === undefined || Number.isNaN(value)) return "n/a";
    const abs = Math.abs(value);
    if (abs >= 1e12) return `$${(value / 1e12).toFixed(2)}T`;
    if (abs >= 1e9) return `$${(value / 1e9).toFixed(2)}B`;
    if (abs >= 1e6) return `$${(value / 1e6).toFixed(2)}M`;
    return `$${value.toLocaleString(undefined, {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    })}`;
  },
  num(value, digits = 1) {
    if (value === null || value === undefined || Number.isNaN(value)) return "n/a";
    return Number(value).toFixed(digits);
  },
  date(value) {
    if (!value) return "n/a";
    return String(value).slice(0, 10);
  },
};
