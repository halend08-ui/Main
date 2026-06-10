import { useState, useEffect, useMemo, useRef, useCallback } from "react";

/* ────────────────────────────────────────────────────────────
   MERIDIAN — a market desk
   Ink-navy terminal, amber phosphor accents, serif masthead.
   All market data is simulated (seeded random walks).
   ──────────────────────────────────────────────────────────── */

const T = {
  ink: "#0B101A",
  panel: "#121927",
  panelSoft: "#0E1420",
  line: "#1F2A3E",
  lineSoft: "#182238",
  text: "#E9EDF5",
  muted: "#8B95A9",
  faint: "#5A6478",
  amber: "#F0A33F",
  amberSoft: "rgba(240,163,63,0.12)",
  up: "#3DD68C",
  down: "#FF6B6B",
  upSoft: "rgba(61,214,140,0.10)",
  downSoft: "rgba(255,107,107,0.10)",
};

/* ── seeded PRNG ── */
function mulberry32(seed) {
  return function () {
    seed |= 0; seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
const hashStr = (s) => { let h = 2166136261; for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); } return h >>> 0; };

/* ── universe ── */
const STOCKS = [
  { sym: "NVDA", name: "NVIDIA Corp.", base: 1187.4, vol: 0.022, mcap: "2.92T", pe: "71.3", sector: "Semiconductors" },
  { sym: "AAPL", name: "Apple Inc.", base: 214.6, vol: 0.011, mcap: "3.29T", pe: "33.4", sector: "Consumer Tech" },
  { sym: "MSFT", name: "Microsoft Corp.", base: 447.2, vol: 0.010, mcap: "3.32T", pe: "38.6", sector: "Software" },
  { sym: "AMZN", name: "Amazon.com Inc.", base: 186.9, vol: 0.014, mcap: "1.94T", pe: "51.2", sector: "E-Commerce" },
  { sym: "GOOGL", name: "Alphabet Inc.", base: 178.3, vol: 0.012, mcap: "2.19T", pe: "27.1", sector: "Internet" },
  { sym: "META", name: "Meta Platforms", base: 504.1, vol: 0.016, mcap: "1.28T", pe: "29.8", sector: "Social Media" },
  { sym: "TSLA", name: "Tesla Inc.", base: 183.0, vol: 0.028, mcap: "583.7B", pe: "46.5", sector: "Automotive" },
  { sym: "AVGO", name: "Broadcom Inc.", base: 1404.8, vol: 0.018, mcap: "653.4B", pe: "49.0", sector: "Semiconductors" },
  { sym: "JPM", name: "JPMorgan Chase", base: 198.5, vol: 0.009, mcap: "569.1B", pe: "12.2", sector: "Banking" },
  { sym: "NFLX", name: "Netflix Inc.", base: 645.8, vol: 0.017, mcap: "278.9B", pe: "44.7", sector: "Streaming" },
];
const INDICES = [
  { sym: "S&P 500", base: 5354.0, vol: 0.006 },
  { sym: "NASDAQ", base: 17188.2, vol: 0.008 },
  { sym: "DOW", base: 38807.3, vol: 0.005 },
  { sym: "RUSSELL 2K", base: 2026.6, vol: 0.009 },
];

const HOLDINGS = [
  { sym: "NVDA", shares: 12, cost: 905.2 },
  { sym: "AAPL", shares: 40, cost: 178.1 },
  { sym: "MSFT", shares: 15, cost: 402.33 },
  { sym: "META", shares: 8, cost: 452.6 },
  { sym: "TSLA", shares: 25, cost: 201.8 },
  { sym: "JPM", shares: 30, cost: 171.45 },
];

/* ── 1,000 generated symbols for the screener ── */
const fmtCap = (n) => (n >= 1e12 ? (n / 1e12).toFixed(2) + "T" : n >= 1e9 ? (n / 1e9).toFixed(1) + "B" : (n / 1e6).toFixed(0) + "M");

const GEN_STOCKS = (() => {
  const rnd = mulberry32(987654321);
  const pre = ["Aero", "Apex", "Astra", "Atlas", "Auric", "Basalt", "Beacon", "Bolt", "Cardinal", "Cedar", "Cipher", "Cobalt", "Crest", "Crown", "Cypress", "Drift", "Echo", "Ember", "Falcon", "Flux", "Forge", "Gale", "Granite", "Halo", "Harbor", "Helio", "Iron", "Juniper", "Keystone", "Kite", "Lattice", "Lumen", "Maple", "Mosaic", "Nimbus", "Novus", "Obsidian", "Onyx", "Orchard", "Pinnacle", "Quanta", "Raven", "Ridge", "Sable", "Sierra", "Solis", "Sterling", "Summit", "Tidal", "Vanta", "Vela", "Verdant", "Vertex", "Wayfarer", "Zenith", "Zephyr"];
  const mid = ["Global", "Pacific", "Quantum", "Prime", "Next", "Core", "First", "Nova", "Micro"];
  const suf = ["Dynamics", "Systems", "Labs", "Therapeutics", "Biosciences", "Energy", "Materials", "Robotics", "Networks", "Logistics", "Capital", "Holdings", "Industries", "Semiconductors", "Analytics", "Foods", "Health", "Mobility", "Aerospace", "Pharma", "Digital", "Grid", "Marine", "Mining", "Media", "Group", "Technologies", "Solutions", "Instruments", "Renewables"];
  const sectorOf = {
    Dynamics: "Industrials", Systems: "Technology", Labs: "Technology", Therapeutics: "Biotech",
    Biosciences: "Biotech", Energy: "Energy", Materials: "Materials", Robotics: "Industrials",
    Networks: "Communications", Logistics: "Industrials", Capital: "Financials", Holdings: "Financials",
    Industries: "Industrials", Semiconductors: "Semiconductors", Analytics: "Technology", Foods: "Consumer",
    Health: "Healthcare", Mobility: "Consumer", Aerospace: "Industrials", Pharma: "Healthcare",
    Digital: "Technology", Grid: "Utilities", Marine: "Industrials", Mining: "Materials",
    Media: "Communications", Group: "Financials", Technologies: "Technology", Solutions: "Technology",
    Instruments: "Healthcare", Renewables: "Energy",
  };
  const letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ";
  const usedSym = new Set(STOCKS.map((s) => s.sym));
  const usedName = new Set(STOCKS.map((s) => s.name));
  const out = [];
  while (out.length < 1000) {
    const p = pre[(rnd() * pre.length) | 0];
    const s = suf[(rnd() * suf.length) | 0];
    const name = rnd() < 0.32 ? `${p} ${mid[(rnd() * mid.length) | 0]} ${s}` : `${p} ${s}`;
    if (usedName.has(name)) continue;
    let sym = (p.slice(0, 2) + s.slice(0, 2)).toUpperCase();
    let guard = 0;
    while (usedSym.has(sym) && guard++ < 60) {
      sym = (sym.length < 5 ? sym : sym.slice(0, 3)) + letters[(rnd() * 26) | 0];
    }
    if (usedSym.has(sym)) continue;
    usedSym.add(sym); usedName.add(name);
    const base = +(4 + Math.pow(rnd(), 1.6) * 950).toFixed(2);
    const vol = 0.008 + rnd() * 0.03;
    const mcapNum = base * (3e7 + rnd() * 3.5e9);
    const pe = rnd() < 0.16 ? "—" : (7 + rnd() * 68).toFixed(1);
    out.push({ sym, name, sector: sectorOf[s], base, vol, mcapNum, mcap: fmtCap(mcapNum), pe });
  }
  return out;
})();

const UNIVERSE = [...STOCKS, ...GEN_STOCKS];
const BY_SYM = Object.fromEntries(UNIVERSE.map((s) => [s.sym, s]));

const RANGES = [
  { id: "1D", pts: 78, span: "Today" },
  { id: "1W", pts: 70, span: "Past week" },
  { id: "1M", pts: 64, span: "Past month" },
  { id: "3M", pts: 66, span: "Past 3 months" },
  { id: "1Y", pts: 120, span: "Past year" },
];

/* ── series generation: anchored random walk ending at base price ── */
function genSeries(sym, rangeId, pts, base, vol) {
  const rnd = mulberry32(hashStr(sym + "·" + rangeId));
  const scale = { "1D": 0.55, "1W": 1.4, "1M": 2.6, "3M": 4.2, "1Y": 8.5 }[rangeId];
  const raw = [0];
  for (let i = 1; i < pts; i++) raw.push(raw[i - 1] + (rnd() - 0.487) * vol * scale);
  const end = raw[raw.length - 1];
  return raw.map((v, i) => base * (1 + v - end * (i / (pts - 1))));
}

const fmt = (n, d = 2) => n.toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
const fmtSign = (n, d = 2) => (n >= 0 ? "+" : "−") + fmt(Math.abs(n), d);

function timeLabels(rangeId, pts) {
  if (rangeId === "1D") {
    const out = []; let m = 9 * 60 + 30;
    for (let i = 0; i < pts; i++) { const t = m + i * 5; const h = Math.floor(t / 60); const mm = t % 60; const h12 = ((h + 11) % 12) + 1; out.push(`${h12}:${String(mm).padStart(2, "0")} ${h < 12 ? "AM" : "PM"}`); }
    return out;
  }
  const days = { "1W": 7, "1M": 30, "3M": 91, "1Y": 365 }[rangeId];
  const now = new Date(2026, 5, 9);
  return Array.from({ length: pts }, (_, i) => {
    const d = new Date(now); d.setDate(d.getDate() - Math.round(days * (1 - i / (pts - 1))));
    return d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: rangeId === "1Y" ? "numeric" : undefined });
  });
}

/* ── sparkline ── */
function Spark({ data, color, w = 96, h = 30 }) {
  const min = Math.min(...data), max = Math.max(...data), r = max - min || 1;
  const pts = data.map((v, i) => `${(i / (data.length - 1)) * w},${h - 2 - ((v - min) / r) * (h - 4)}`).join(" ");
  const id = useRef("sp" + Math.random().toString(36).slice(2, 8)).current;
  return (
    <svg width={w} height={h} style={{ display: "block", overflow: "visible" }} aria-hidden="true">
      <defs>
        <linearGradient id={id} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.28" />
          <stop offset="100%" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      <polygon points={`0,${h} ${pts} ${w},${h}`} fill={`url(#${id})`} />
      <polyline points={pts} fill="none" stroke={color} strokeWidth="1.5" strokeLinejoin="round" />
    </svg>
  );
}

/* ── main chart with crosshair ── */
function MainChart({ data, labels, color, prevRef }) {
  const wrapRef = useRef(null);
  const [size, setSize] = useState({ w: 720, h: 320 });
  const [hover, setHover] = useState(null);

  useEffect(() => {
    const el = wrapRef.current; if (!el) return;
    const ro = new ResizeObserver(() => setSize({ w: el.clientWidth, h: el.clientHeight }));
    ro.observe(el); return () => ro.disconnect();
  }, []);

  const PAD = { l: 8, r: 64, t: 14, b: 26 };
  const W = size.w, H = size.h;
  const iw = Math.max(W - PAD.l - PAD.r, 10), ih = Math.max(H - PAD.t - PAD.b, 10);
  let min = Math.min(...data), max = Math.max(...data);
  if (prevRef != null) { min = Math.min(min, prevRef); max = Math.max(max, prevRef); }
  const pad = (max - min) * 0.08 || 1; min -= pad; max += pad;
  const X = (i) => PAD.l + (i / (data.length - 1)) * iw;
  const Y = (v) => PAD.t + (1 - (v - min) / (max - min)) * ih;

  const line = data.map((v, i) => `${X(i)},${Y(v)}`).join(" ");
  const ticks = Array.from({ length: 4 }, (_, i) => min + ((i + 0.5) / 4) * (max - min));
  const gid = useRef("g" + Math.random().toString(36).slice(2, 8)).current;

  const onMove = useCallback((e) => {
    const rect = wrapRef.current.getBoundingClientRect();
    const x = (e.touches ? e.touches[0].clientX : e.clientX) - rect.left;
    const i = Math.round(((x - PAD.l) / iw) * (data.length - 1));
    setHover(Math.max(0, Math.min(data.length - 1, i)));
  }, [data.length, iw]);

  const hv = hover != null ? data[hover] : null;
  const first = data[0];
  const tipLeft = hover != null ? Math.min(Math.max(X(hover) - 70, 4), W - 150) : 0;

  return (
    <div
      ref={wrapRef}
      style={{ position: "relative", width: "100%", height: "100%", cursor: "crosshair", touchAction: "pan-y" }}
      onMouseMove={onMove}
      onTouchMove={onMove}
      onMouseLeave={() => setHover(null)}
      onTouchEnd={() => setHover(null)}
    >
      <svg width="100%" height="100%" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" style={{ display: "block" }}>
        <defs>
          <linearGradient id={gid} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={color} stopOpacity="0.22" />
            <stop offset="100%" stopColor={color} stopOpacity="0" />
          </linearGradient>
        </defs>
        {ticks.map((t, i) => (
          <g key={i}>
            <line x1={PAD.l} x2={W - PAD.r} y1={Y(t)} y2={Y(t)} stroke={T.lineSoft} strokeDasharray="2 5" />
            <text x={W - PAD.r + 8} y={Y(t) + 3.5} fill={T.faint} fontSize="10.5" fontFamily="'IBM Plex Mono', monospace">{fmt(t, t > 1000 ? 0 : 2)}</text>
          </g>
        ))}
        {prevRef != null && (
          <g>
            <line x1={PAD.l} x2={W - PAD.r} y1={Y(prevRef)} y2={Y(prevRef)} stroke={T.faint} strokeDasharray="1 4" />
            <text x={W - PAD.r + 8} y={Y(prevRef) + 3.5} fill={T.muted} fontSize="10.5" fontFamily="'IBM Plex Mono', monospace">prev</text>
          </g>
        )}
        <polygon points={`${X(0)},${PAD.t + ih} ${line} ${X(data.length - 1)},${PAD.t + ih}`} fill={`url(#${gid})`} />
        <polyline points={line} fill="none" stroke={color} strokeWidth="1.8" strokeLinejoin="round" />
        <circle cx={X(data.length - 1)} cy={Y(data[data.length - 1])} r="3" fill={color}>
          <animate attributeName="opacity" values="1;0.35;1" dur="1.6s" repeatCount="indefinite" />
        </circle>
        {hover != null && (
          <g>
            <line x1={X(hover)} x2={X(hover)} y1={PAD.t} y2={PAD.t + ih} stroke={T.muted} strokeWidth="1" strokeDasharray="3 3" />
            <circle cx={X(hover)} cy={Y(hv)} r="4" fill={T.ink} stroke={color} strokeWidth="2" />
          </g>
        )}
        {[0, 0.25, 0.5, 0.75, 1].map((f) => {
          const i = Math.round(f * (data.length - 1));
          return (
            <text key={f} x={X(i)} y={H - 8} fill={T.faint} fontSize="10.5" fontFamily="'IBM Plex Mono', monospace"
              textAnchor={f === 0 ? "start" : f === 1 ? "end" : "middle"}>{labels[i]}</text>
          );
        })}
      </svg>
      {hover != null && (
        <div style={{
          position: "absolute", top: 6, left: tipLeft, pointerEvents: "none",
          background: "rgba(11,16,26,0.92)", border: `1px solid ${T.line}`, borderRadius: 6,
          padding: "7px 10px", fontFamily: "'IBM Plex Mono', monospace", fontSize: 12, lineHeight: 1.5,
          backdropFilter: "blur(4px)", boxShadow: "0 6px 20px rgba(0,0,0,0.45)", whiteSpace: "nowrap",
        }}>
          <div style={{ color: T.muted, fontSize: 10.5, letterSpacing: "0.04em" }}>{labels[hover]}</div>
          <div style={{ color: T.text, fontSize: 14 }}>${fmt(hv)}</div>
          <div style={{ color: hv >= first ? T.up : T.down, fontSize: 11 }}>
            {fmtSign(hv - first)} ({fmtSign(((hv - first) / first) * 100)}%) in range
          </div>
        </div>
      )}
    </div>
  );
}

/* ── ticker tape ── */
function Tape({ quotes }) {
  const items = [...quotes, ...quotes];
  return (
    <div className="tape" aria-label="Scrolling stock ticker">
      <div className="tape-inner">
        {items.map((q, i) => (
          <span key={i} className="tape-item">
            <span style={{ color: T.text, fontWeight: 600 }}>{q.sym}</span>
            <span style={{ color: T.muted, margin: "0 8px" }}>{fmt(q.price)}</span>
            <span style={{ color: q.chgPct >= 0 ? T.up : T.down }}>
              {q.chgPct >= 0 ? "▲" : "▼"} {fmt(Math.abs(q.chgPct))}%
            </span>
          </span>
        ))}
      </div>
    </div>
  );
}

/* ── rolling odometer number ── */
function Roller({ value, color = T.text }) {
  return (
    <span style={{ display: "inline-flex", alignItems: "flex-start", color, fontVariantNumeric: "tabular-nums" }} aria-label={value}>
      {value.split("").map((c, i) =>
        /\d/.test(c) ? (
          <span key={i} className="roll-window" aria-hidden="true">
            <span className="roll-col" style={{ transform: `translateY(${-Number(c) * 10}%)` }}>
              {[0, 1, 2, 3, 4, 5, 6, 7, 8, 9].map((d) => (
                <span key={d} className="roll-digit">{d}</span>
              ))}
            </span>
          </span>
        ) : (
          <span key={i} className="roll-digit" aria-hidden="true">{c}</span>
        )
      )}
    </span>
  );
}

/* ── relevancy scoring for screener search ── */
function relScore(r, q) {
  const sym = r.sym.toLowerCase(), name = r.name.toLowerCase(), sec = r.sector.toLowerCase();
  if (sym === q) return 100;
  if (sym.startsWith(q)) return 80;
  if (sym.includes(q)) return 60;
  if (name.split(" ").some((w) => w.startsWith(q))) return 50;
  if (name.includes(q)) return 34;
  if (sec.startsWith(q)) return 22;
  if (sec.includes(q)) return 14;
  return 0;
}

/* ── market screener ── */
const SORTS = [
  { id: "relevance", label: "Most relevant" },
  { id: "gainers", label: "Top gainers" },
  { id: "losers", label: "Top losers" },
  { id: "mcap", label: "Market cap" },
  { id: "alpha", label: "A–Z" },
];
const PAGE = 25;

function ScreenerPanel({ watch, onAdd, onRemove, onOpen }) {
  const [query, setQuery] = useState("");
  const [sector, setSector] = useState("All");
  const [sort, setSort] = useState("relevance");
  const [page, setPage] = useState(0);

  const rows = useMemo(() => GEN_STOCKS.map((s) => {
    const r = mulberry32(hashStr(s.sym + "day"));
    const dayMove = (r() - 0.45) * s.vol * 2.4;
    const price = s.base * (1 + dayMove);
    const prevClose = s.base * (1 - s.vol * 0.3 * (r() - 0.5));
    const chgPct = ((price - prevClose) / prevClose) * 100;
    return { ...s, price, prevClose, chgPct, heat: Math.abs(chgPct) * Math.log10(s.mcapNum) };
  }), []);

  const sectors = useMemo(() => ["All", ...Array.from(new Set(GEN_STOCKS.map((s) => s.sector))).sort()], []);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    let list = sector === "All" ? rows : rows.filter((r) => r.sector === sector);
    if (q) {
      list = list
        .map((r) => ({ r, score: relScore(r, q) }))
        .filter((x) => x.score > 0)
        .sort((a, b) => b.score - a.score || b.r.heat - a.r.heat)
        .map((x) => x.r);
      if (sort === "relevance") return list;
    }
    const sorted = [...list];
    if (sort === "relevance") sorted.sort((a, b) => b.heat - a.heat);
    if (sort === "gainers") sorted.sort((a, b) => b.chgPct - a.chgPct);
    if (sort === "losers") sorted.sort((a, b) => a.chgPct - b.chgPct);
    if (sort === "mcap") sorted.sort((a, b) => b.mcapNum - a.mcapNum);
    if (sort === "alpha") sorted.sort((a, b) => a.sym.localeCompare(b.sym));
    return sorted;
  }, [rows, query, sector, sort]);

  useEffect(() => { setPage(0); }, [query, sector, sort]);
  const pages = Math.max(1, Math.ceil(filtered.length / PAGE));
  const safePage = Math.min(page, pages - 1);
  const slice = filtered.slice(safePage * PAGE, (safePage + 1) * PAGE);

  return (
    <section style={{ background: T.panel, border: `1px solid ${T.line}`, borderRadius: 11, overflow: "hidden" }}>
      <div style={{ padding: "15px 18px 13px", borderBottom: `1px solid ${T.line}`, display: "grid", gap: 12 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", flexWrap: "wrap", gap: 8 }}>
          <div>
            <h3 style={{ margin: 0, fontFamily: "'Fraunces', serif", fontWeight: 600, fontSize: 16.5 }}>Screener</h3>
            <div style={{ fontSize: 11, color: T.faint, marginTop: 3 }}>Click any row for full details, past trends, and an AI verdict</div>
          </div>
          <span style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 11, color: T.faint }}>
            {filtered.length.toLocaleString()} of {rows.length.toLocaleString()} symbols
            {query.trim() && sort === "relevance" && " · ranked by relevance"}
          </span>
        </div>
        <div style={{ display: "flex", gap: 9, flexWrap: "wrap" }}>
          <input
            className="chat-input"
            style={{ minWidth: 200 }}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search symbol, company, or sector…"
            aria-label="Search the screener"
          />
          <select className="sel" value={sort} onChange={(e) => setSort(e.target.value)} aria-label="Sort screener">
            {SORTS.map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}
          </select>
        </div>
        <div className="chip-row">
          {sectors.map((s) => (
            <button key={s} className="chip" data-on={s === sector} onClick={() => setSector(s)}>{s}</button>
          ))}
        </div>
      </div>

      <div className="scr-grid" style={{ padding: "9px 18px", borderBottom: `1px solid ${T.line}`, background: T.panelSoft }}>
        {[["Symbol", "left"], ["Sector", "right", true], ["Price", "right"], ["Day", "right"], ["Mkt cap", "right", true], ["P/E", "right", true], ["", "right"]].map(([h, align, hide], i) => (
          <div key={i} className={hide ? "hide-sm" : ""} style={{ fontSize: 10.5, letterSpacing: "0.12em", textTransform: "uppercase", color: T.faint, textAlign: align }}>{h}</div>
        ))}
      </div>

      {slice.length === 0 ? (
        <div style={{ padding: "26px 18px", color: T.muted, fontSize: 13 }}>
          Nothing matches "{query}". Try a shorter search, or clear the sector filter.
        </div>
      ) : (
        slice.map((r) => {
          const inWatch = watch.includes(r.sym);
          return (
            <div key={r.sym} className="scr-grid scr-row" style={{ padding: "10px 18px", cursor: "pointer" }}
              role="button" tabIndex={0}
              aria-label={`Open details for ${r.name}`}
              onClick={() => onOpen(r.sym, r)}
              onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onOpen(r.sym, r); } }}>
              <div style={{ minWidth: 0 }}>
                <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontWeight: 600, fontSize: 13 }}>{r.sym}</div>
                <div style={{ fontSize: 11, color: T.muted, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{r.name}</div>
              </div>
              <div className="hide-sm" style={{ textAlign: "right", fontSize: 11.5, color: T.muted }}>{r.sector}</div>
              <div style={{ textAlign: "right", fontFamily: "'IBM Plex Mono', monospace", fontSize: 12.5 }}>${fmt(r.price)}</div>
              <div style={{ textAlign: "right", fontFamily: "'IBM Plex Mono', monospace", fontSize: 12.5, color: r.chgPct >= 0 ? T.up : T.down }}>
                {r.chgPct >= 0 ? "▲" : "▼"} {fmt(Math.abs(r.chgPct))}%
              </div>
              <div className="hide-sm" style={{ textAlign: "right", fontFamily: "'IBM Plex Mono', monospace", fontSize: 12, color: T.muted }}>${r.mcap}</div>
              <div className="hide-sm" style={{ textAlign: "right", fontFamily: "'IBM Plex Mono', monospace", fontSize: 12, color: T.muted }}>{r.pe}</div>
              <button
                className="add-btn"
                data-in={inWatch}
                onClick={(e) => { e.stopPropagation(); inWatch ? onRemove(r.sym) : onAdd(r.sym); }}
                aria-label={inWatch ? `Remove ${r.sym} from watchlist` : `Add ${r.sym} to watchlist`}
              >
                {inWatch ? "− Remove" : "+ Watch"}
              </button>
            </div>
          );
        })
      )}

      <div style={{ padding: "12px 18px", display: "flex", justifyContent: "space-between", alignItems: "center", borderTop: `1px solid ${T.line}` }}>
        <span style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 11, color: T.faint }}>
          {filtered.length === 0 ? "0 results" : `${safePage * PAGE + 1}–${Math.min((safePage + 1) * PAGE, filtered.length)} of ${filtered.length.toLocaleString()}`}
        </span>
        <div style={{ display: "flex", gap: 7, alignItems: "center" }}>
          <button className="pg-btn" onClick={() => setPage((p) => Math.max(0, p - 1))} disabled={safePage === 0}>← Prev</button>
          <span style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 11, color: T.muted }}>{safePage + 1} / {pages}</span>
          <button className="pg-btn" onClick={() => setPage((p) => Math.min(pages - 1, p + 1))} disabled={safePage >= pages - 1}>Next →</button>
        </div>
      </div>
    </section>
  );
}

/* ── portfolio with simulated P&L ── */
function PortfolioPanel({ quotes }) {
  const bySym = Object.fromEntries(quotes.map((q) => [q.sym, q]));
  const rows = HOLDINGS.map((h) => {
    const q = bySym[h.sym];
    const mv = q.price * h.shares;
    const costBasis = h.cost * h.shares;
    const pl = mv - costBasis;
    const dayPl = (q.price - q.prevClose) * h.shares;
    return { ...h, q, mv, costBasis, pl, plPct: (pl / costBasis) * 100, dayPl };
  });
  const totMv = rows.reduce((a, r) => a + r.mv, 0);
  const totCost = rows.reduce((a, r) => a + r.costBasis, 0);
  const totPl = totMv - totCost;
  const totDay = rows.reduce((a, r) => a + r.dayPl, 0);

  const Stat = ({ label, children }) => (
    <div style={{ background: T.panelSoft, border: `1px solid ${T.lineSoft}`, borderRadius: 8, padding: "13px 15px" }}>
      <div style={{ fontSize: 10.5, letterSpacing: "0.12em", textTransform: "uppercase", color: T.faint }}>{label}</div>
      <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 21, fontWeight: 600, marginTop: 5 }}>{children}</div>
    </div>
  );

  return (
    <section style={{ background: T.panel, border: `1px solid ${T.line}`, borderRadius: 11, overflow: "hidden" }}>
      <div style={{ padding: "15px 18px", borderBottom: `1px solid ${T.line}`, display: "flex", justifyContent: "space-between", alignItems: "baseline", flexWrap: "wrap", gap: 8 }}>
        <h3 style={{ margin: 0, fontFamily: "'Fraunces', serif", fontWeight: 600, fontSize: 16.5 }}>Portfolio</h3>
        <span style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 11, color: T.faint }}>
          {rows.length} positions · simulated
        </span>
      </div>

      <div style={{ padding: "16px 18px", display: "grid", gap: 16 }}>
        <div className="pf-sum">
          <Stat label="Total value"><Roller value={"$" + fmt(totMv)} /></Stat>
          <Stat label="Total P&L">
            <span style={{ color: totPl >= 0 ? T.up : T.down }}>
              {fmtSign(totPl)} <span style={{ fontSize: 13, fontWeight: 400 }}>({fmtSign((totPl / totCost) * 100)}%)</span>
            </span>
          </Stat>
          <Stat label="Today's P&L">
            <span style={{ color: totDay >= 0 ? T.up : T.down }}><Roller value={fmtSign(totDay)} color={totDay >= 0 ? T.up : T.down} /></span>
          </Stat>
        </div>

        <div>
          <div style={{ display: "flex", height: 9, borderRadius: 5, overflow: "hidden", border: `1px solid ${T.lineSoft}` }}>
            {rows.map((r, i) => (
              <div key={r.sym} title={`${r.sym} · ${fmt((r.mv / totMv) * 100, 1)}%`}
                style={{ width: `${(r.mv / totMv) * 100}%`, background: r.pl >= 0 ? T.up : T.down, opacity: 0.35 + (i % 3) * 0.22, transition: "width 0.6s ease" }} />
            ))}
          </div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: "4px 16px", marginTop: 8 }}>
            {rows.map((r) => (
              <span key={r.sym} style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 10.5, color: T.muted }}>
                {r.sym} {fmt((r.mv / totMv) * 100, 1)}%
              </span>
            ))}
          </div>
        </div>

        <div>
          <div className="pf-grid" style={{ padding: "0 2px 9px", borderBottom: `1px solid ${T.line}` }}>
            {["Position", "Avg cost", "Mkt value", "Today", "Total P&L"].map((h, i) => (
              <div key={h} className={i === 1 || i === 3 ? "hide-sm" : ""} style={{ fontSize: 10.5, letterSpacing: "0.12em", textTransform: "uppercase", color: T.faint, textAlign: i === 0 ? "left" : "right" }}>{h}</div>
            ))}
          </div>
          {rows.map((r) => (
            <div key={r.sym} className="pf-grid" style={{ padding: "11px 2px", borderBottom: `1px solid ${T.lineSoft}`, fontFamily: "'IBM Plex Mono', monospace", fontSize: 12.5 }}>
              <div>
                <div style={{ fontWeight: 600, fontSize: 13 }}>{r.sym}</div>
                <div style={{ color: T.muted, fontSize: 11 }}>{r.shares} sh</div>
              </div>
              <div className="hide-sm" style={{ textAlign: "right", color: T.muted }}>${fmt(r.cost)}</div>
              <div style={{ textAlign: "right" }}><Roller value={"$" + fmt(r.mv)} /></div>
              <div className="hide-sm" style={{ textAlign: "right", color: r.dayPl >= 0 ? T.up : T.down }}>{fmtSign(r.dayPl)}</div>
              <div style={{ textAlign: "right", color: r.pl >= 0 ? T.up : T.down }}>
                {fmtSign(r.pl)} <span style={{ color: T.faint }}>·</span> {fmtSign(r.plPct)}%
              </div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

/* ── AI desk analyst (Claude-powered) ── */
function AnalystPanel({ quotes, holdings }) {
  const [state, setState] = useState("idle");
  const [signals, setSignals] = useState([]);
  const [summary, setSummary] = useState("");
  const [ranAt, setRanAt] = useState(null);
  const [msgs, setMsgs] = useState([]);
  const [draft, setDraft] = useState("");
  const [chatBusy, setChatBusy] = useState(false);
  const chatEndRef = useRef(null);

  useEffect(() => { chatEndRef.current?.scrollIntoView({ behavior: "smooth", block: "end" }); }, [msgs, chatBusy]);

  const contextBlock = () => {
    const snapshot = quotes.map((q) => ({
      symbol: q.sym, name: q.name, sector: q.sector,
      price: +q.price.toFixed(2), dayChangePct: +q.chgPct.toFixed(2),
      marketCap: q.mcap, peRatio: q.pe,
    }));
    return `You are the desk analyst on a market dashboard called Meridian. All data is simulated demo data.

Current watchlist snapshot:
${JSON.stringify(snapshot)}

User's simulated portfolio (shares, avg cost):
${JSON.stringify(holdings)}

${signals.length ? `Your most recent signals were:\n${JSON.stringify(signals)}` : "You have not issued signals yet this session."}

Answer the user's questions like a sharp, plain-spoken analyst. Keep replies under 120 words, no markdown formatting, no bullet lists — just tight prose. Remember this is demo data, so frame everything as analysis of the simulated tape, not real investment advice.`;
  };

  const sendChat = async () => {
    const text = draft.trim();
    if (!text || chatBusy) return;
    const next = [...msgs, { role: "user", content: text }];
    setMsgs(next);
    setDraft("");
    setChatBusy(true);
    try {
      const res = await fetch("https://api.anthropic.com/v1/messages", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          model: "claude-sonnet-4-20250514",
          max_tokens: 1000,
          messages: [
            { role: "user", content: contextBlock() },
            { role: "assistant", content: "Understood. I'm on the desk — ask away." },
            ...next,
          ],
        }),
      });
      const data = await res.json();
      const reply = (data.content || []).filter((b) => b.type === "text").map((b) => b.text).join("\n").trim();
      setMsgs((m) => [...m, { role: "assistant", content: reply || "I lost the thread there — try asking again." }]);
    } catch (err) {
      console.error("Chat error:", err);
      setMsgs((m) => [...m, { role: "assistant", content: "Connection dropped before I could answer. Send that again." }]);
    } finally {
      setChatBusy(false);
    }
  };

  const run = async () => {
    setState("loading");
    const snapshot = quotes.map((q) => ({
      symbol: q.sym, name: q.name, sector: q.sector,
      price: +q.price.toFixed(2), dayChangePct: +q.chgPct.toFixed(2),
      marketCap: q.mcap, peRatio: q.pe,
    }));
    const prompt = `You are a sharp but level-headed equity desk analyst reviewing a watchlist.

Here is the current snapshot (simulated demo data):
${JSON.stringify(snapshot, null, 2)}

For each symbol, give a signal based on the momentum, valuation (P/E), and sector picture implied by this snapshot. Be opinionated but reasonable, and vary your calls — do not mark everything HOLD.

Respond with ONLY raw JSON, no markdown fences, no preamble, exactly this shape:
{
  "summary": "<2 sentence read of the overall tape>",
  "signals": [
    { "sym": "<symbol>", "signal": "BUY" | "HOLD" | "SELL", "confidence": <1-5>, "note": "<one punchy sentence of reasoning>" }
  ]
}`;
    try {
      const res = await fetch("https://api.anthropic.com/v1/messages", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          model: "claude-sonnet-4-20250514",
          max_tokens: 1000,
          messages: [{ role: "user", content: prompt }],
        }),
      });
      const data = await res.json();
      const text = (data.content || []).filter((b) => b.type === "text").map((b) => b.text).join("\n");
      const clean = text.replace(/```json|```/g, "").trim();
      const parsed = JSON.parse(clean);
      if (!Array.isArray(parsed.signals)) throw new Error("bad shape");
      setSignals(parsed.signals);
      setSummary(parsed.summary || "");
      setRanAt(new Date());
      setState("done");
    } catch (err) {
      console.error("Analyst error:", err);
      setState("error");
    }
  };

  const sigStyle = (s) =>
    s === "BUY" ? { color: T.up, bg: T.upSoft, border: "rgba(61,214,140,0.35)" }
    : s === "SELL" ? { color: T.down, bg: T.downSoft, border: "rgba(255,107,107,0.35)" }
    : { color: T.amber, bg: T.amberSoft, border: "rgba(240,163,63,0.35)" };

  return (
    <section style={{ background: T.panel, border: `1px solid ${T.line}`, borderRadius: 11, overflow: "hidden" }}>
      <div style={{ padding: "15px 18px", borderBottom: `1px solid ${T.line}`, display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 10 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <span style={{
            width: 26, height: 26, borderRadius: 7, background: T.amberSoft, border: `1px solid rgba(240,163,63,0.35)`,
            display: "inline-flex", alignItems: "center", justifyContent: "center", color: T.amber, fontSize: 13,
          }}>✦</span>
          <div>
            <h3 style={{ margin: 0, fontFamily: "'Fraunces', serif", fontWeight: 600, fontSize: 16.5 }}>Desk Analyst</h3>
            <div style={{ fontSize: 11, color: T.faint }}>
              Claude reads the tape and calls buy / hold / sell on each name
              {ranAt && <span> · last run {ranAt.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" })}</span>}
            </div>
          </div>
        </div>
        <button className="run-btn" onClick={run} disabled={state === "loading"}>
          {state === "loading" ? "Reading the tape…" : state === "done" ? "Re-run analysis" : "Run analysis"}
        </button>
      </div>

      <div style={{ padding: "16px 18px" }}>
        {state === "idle" && (
          <div style={{ color: T.muted, fontSize: 13, lineHeight: 1.6 }}>
            Run the analyst to get a signal on every watchlist name — a call, a confidence read, and one line of reasoning each.
          </div>
        )}
        {state === "loading" && (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(210px, 1fr))", gap: 10 }}>
            {Array.from({ length: 10 }).map((_, i) => (
              <div key={i} className="shimmer" style={{ height: 86, borderRadius: 8, animationDelay: `${i * 0.08}s` }} />
            ))}
          </div>
        )}
        {state === "error" && (
          <div style={{ color: T.down, fontSize: 13 }}>
            The analyst couldn't complete the read. Check your connection and run it again.
          </div>
        )}
        {state === "done" && (
          <>
            {summary && (
              <p style={{ margin: "0 0 14px", color: T.text, fontSize: 13.5, lineHeight: 1.65, borderLeft: `2px solid ${T.amber}`, paddingLeft: 12 }}>
                {summary}
              </p>
            )}
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(210px, 1fr))", gap: 10 }}>
              {signals.map((s, i) => {
                const st = sigStyle(s.signal);
                return (
                  <div key={s.sym} className="sig-card" style={{ animationDelay: `${i * 0.06}s`, background: T.panelSoft, border: `1px solid ${T.lineSoft}`, borderRadius: 8, padding: "11px 13px" }}>
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                      <span style={{ fontFamily: "'IBM Plex Mono', monospace", fontWeight: 600, fontSize: 13.5 }}>{s.sym}</span>
                      <span style={{
                        fontFamily: "'IBM Plex Mono', monospace", fontSize: 10.5, letterSpacing: "0.1em",
                        color: st.color, background: st.bg, border: `1px solid ${st.border}`,
                        padding: "2px 8px", borderRadius: 4,
                      }}>{s.signal}</span>
                    </div>
                    <div style={{ display: "flex", gap: 3, margin: "8px 0 7px" }} aria-label={`Confidence ${s.confidence} of 5`}>
                      {[1, 2, 3, 4, 5].map((d) => (
                        <span key={d} style={{ width: 14, height: 3, borderRadius: 2, background: d <= s.confidence ? st.color : T.line }} />
                      ))}
                    </div>
                    <div style={{ fontSize: 11.5, color: T.muted, lineHeight: 1.55 }}>{s.note}</div>
                  </div>
                );
              })}
            </div>
          </>
        )}

        <div style={{ marginTop: 18, borderTop: `1px solid ${T.line}`, paddingTop: 15 }}>
          <div style={{ fontSize: 10.5, letterSpacing: "0.12em", textTransform: "uppercase", color: T.faint, marginBottom: 10 }}>
            Ask the analyst
          </div>
          {msgs.length > 0 && (
            <div className="chat-box" style={{ marginBottom: 11 }}>
              {msgs.map((m, i) => (
                <div key={i} className={`bubble ${m.role === "user" ? "bubble-user" : "bubble-ai"}`}>{m.content}</div>
              ))}
              {chatBusy && (
                <div className="bubble bubble-ai typing" aria-label="Analyst is typing">
                  <span /><span /><span />
                </div>
              )}
              <div ref={chatEndRef} />
            </div>
          )}
          <div style={{ display: "flex", gap: 9 }}>
            <input
              className="chat-input"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") sendChat(); }}
              placeholder={signals.length ? "Why are you bearish on TSLA? What would change your mind?" : "Ask about any name on the tape, or run the analysis first…"}
              aria-label="Ask the analyst a question"
            />
            <button className="run-btn" onClick={sendChat} disabled={chatBusy || !draft.trim()}>
              {chatBusy ? "…" : "Ask"}
            </button>
          </div>
        </div>

        <div style={{ marginTop: 14, fontSize: 10.5, color: T.faint, lineHeight: 1.5 }}>
          AI-generated takes on simulated demo data — for illustration only, not investment advice. Do your own research before trading real money.
        </div>
      </div>
    </section>
  );
}

/* ── per-stock AI report cache ── */
const REPORT_CACHE = new Map();

/* ── stock detail modal ── */
function StockModal({ sym, quote, inWatch, onAdd, onRemove, onClose }) {
  const def = BY_SYM[sym];
  const [range, setRange] = useState("3M");
  const [rep, setRep] = useState(REPORT_CACHE.get(sym) || { status: "idle" });
  const dialogRef = useRef(null);
  const lastFocus = useRef(null);

  useEffect(() => {
    lastFocus.current = document.activeElement;
    dialogRef.current?.focus();
    const onKey = (e) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
      lastFocus.current?.focus?.();
    };
  }, [onClose]);

  const perf = useMemo(() => RANGES.map((r) => {
    const s = genSeries(sym, r.id, r.pts, quote.prevClose, def.vol);
    const first = r.id === "1D" ? quote.prevClose : s[0];
    return { id: r.id, span: r.span, chgPct: ((quote.price - first) / first) * 100 };
  }), [sym, quote.price, quote.prevClose, def.vol]);

  const rangeDef = RANGES.find((r) => r.id === range);
  const series = useMemo(() => {
    const s = genSeries(sym, range, rangeDef.pts, quote.prevClose, def.vol);
    s[s.length - 1] = quote.price;
    if (range === "1D") s[0] = quote.prevClose;
    return s;
  }, [sym, range, rangeDef.pts, quote.price, quote.prevClose, def.vol]);
  const labels = useMemo(() => timeLabels(range, rangeDef.pts), [range, rangeDef.pts]);

  const deep = useMemo(() => {
    const r = mulberry32(hashStr(sym + "deep"));
    const yr = genSeries(sym, "1Y", 120, quote.prevClose, def.vol);
    const hi52 = Math.max(...yr, quote.price), lo52 = Math.min(...yr, quote.price);
    return {
      hi52, lo52,
      pos52: ((quote.price - lo52) / (hi52 - lo52)) * 100,
      beta: (0.55 + r() * 1.5).toFixed(2),
      divYield: r() < 0.45 ? "—" : (r() * 3.1).toFixed(2) + "%",
      avgVol: (4 + r() * 55).toFixed(1) + "M",
      eps: (quote.price / (parseFloat(def.pe) || 25)).toFixed(2),
    };
  }, [sym, quote.price, quote.prevClose, def.vol, def.pe]);

  const trendUp = quote.chgPct >= 0;

  const runReport = async () => {
    setRep({ status: "loading" });
    const prompt = `You are an equity analyst writing a quick verdict card for one stock on a demo dashboard (all data simulated).

Stock: ${def.name} (${sym}), sector ${def.sector}.
Price $${quote.price.toFixed(2)}, day change ${quote.chgPct.toFixed(2)}%.
Performance: ${perf.map((p) => `${p.id}: ${p.chgPct.toFixed(1)}%`).join(", ")}.
Market cap $${def.mcap}, P/E ${def.pe}, 52-week range $${deep.lo52.toFixed(2)}–$${deep.hi52.toFixed(2)} (currently at ${deep.pos52.toFixed(0)}% of that range), beta ${deep.beta}, dividend yield ${deep.divYield}.

Weigh momentum, valuation, and where it sits in its 52-week range. Pick exactly one verdict and commit to it.

Respond with ONLY raw JSON, no fences, no preamble:
{
  "verdict": "BUY_NOW" | "WAIT" | "AVOID",
  "confidence": <1-5>,
  "headline": "<one short punchy line>",
  "reasoning": "<2-3 plain sentences explaining the call>",
  "watchFor": "<one sentence: what would change this verdict>"
}`;
    try {
      const res = await fetch("https://api.anthropic.com/v1/messages", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ model: "claude-sonnet-4-20250514", max_tokens: 1000, messages: [{ role: "user", content: prompt }] }),
      });
      const data = await res.json();
      const text = (data.content || []).filter((b) => b.type === "text").map((b) => b.text).join("\n");
      const parsed = JSON.parse(text.replace(/```json|```/g, "").trim());
      const done = { status: "done", data: parsed };
      REPORT_CACHE.set(sym, done);
      setRep(done);
    } catch (err) {
      console.error("Report error:", err);
      setRep({ status: "error" });
    }
  };

  const VERDICTS = {
    BUY_NOW: { label: "Buy now", color: T.up, bg: T.upSoft, border: "rgba(61,214,140,0.4)" },
    WAIT: { label: "Wait", color: T.amber, bg: T.amberSoft, border: "rgba(240,163,63,0.4)" },
    AVOID: { label: "Don't buy", color: T.down, bg: T.downSoft, border: "rgba(255,107,107,0.4)" },
  };

  const blurb = {
    Technology: "builds software and digital infrastructure", Biotech: "develops new medicines and therapies",
    Healthcare: "provides health products and services", Energy: "produces and distributes energy",
    Industrials: "makes industrial equipment and services", Financials: "offers financial and investment services",
    Materials: "supplies raw and engineered materials", Consumer: "sells consumer products and services",
    Communications: "runs media and network platforms", Utilities: "operates power and grid services",
    Semiconductors: "designs and manufactures chips",
  }[def.sector] || "operates across its sector";

  const Row = ({ k, v }) => (
    <div style={{ display: "flex", justifyContent: "space-between", padding: "8px 0", borderBottom: `1px solid ${T.lineSoft}` }}>
      <span style={{ fontSize: 12, color: T.muted }}>{k}</span>
      <span style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 12.5 }}>{v}</span>
    </div>
  );

  return (
    <div className="overlay" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="modal" role="dialog" aria-modal="true" aria-label={`${def.name} details`} ref={dialogRef} tabIndex={-1}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 12, padding: "20px 22px 0" }}>
          <div>
            <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
              <h2 style={{ margin: 0, fontFamily: "'Fraunces', serif", fontWeight: 600, fontSize: 21 }}>{def.name}</h2>
              <span style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 12, color: T.amber, background: T.amberSoft, padding: "2px 8px", borderRadius: 4 }}>{sym}</span>
            </div>
            <div style={{ fontSize: 12.5, color: T.muted, marginTop: 5, lineHeight: 1.55 }}>
              {def.sector} · {def.name} {blurb}.
            </div>
          </div>
          <button className="icon-btn" onClick={onClose} aria-label="Close details">✕</button>
        </div>

        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 12, padding: "14px 22px 0" }}>
          <div>
            <span style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 26, fontWeight: 600 }}>${fmt(quote.price)}</span>
            <span style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 13, marginLeft: 10, color: trendUp ? T.up : T.down }}>
              {trendUp ? "▲" : "▼"} {fmtSign(quote.chgPct)}% today
            </span>
          </div>
          <button className="add-btn" style={{ width: "auto", padding: "8px 16px" }} data-in={inWatch}
            onClick={() => (inWatch ? onRemove(sym) : onAdd(sym))}>
            {inWatch ? "− Remove from watchlist" : "+ Add to watchlist"}
          </button>
        </div>

        <div style={{ padding: "10px 22px 0" }}>
          <div style={{ display: "flex", gap: 4, marginBottom: 4 }}>
            {RANGES.map((r) => (
              <button key={r.id} className="rng" data-on={r.id === range} onClick={() => setRange(r.id)}>{r.id}</button>
            ))}
          </div>
          <div style={{ height: 230 }}>
            <MainChart data={series} labels={labels} color={series[series.length - 1] >= series[0] ? T.up : T.down}
              prevRef={range === "1D" ? quote.prevClose : null} />
          </div>
        </div>

        <div style={{ padding: "16px 22px 0" }}>
          <h3 style={{ margin: "0 0 10px", fontSize: 11, letterSpacing: "0.13em", textTransform: "uppercase", color: T.muted, fontWeight: 500 }}>Past trends</h3>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(96px, 1fr))", gap: 9 }}>
            {perf.map((p) => (
              <div key={p.id} style={{ background: T.panelSoft, border: `1px solid ${T.lineSoft}`, borderRadius: 8, padding: "10px 12px" }}>
                <div style={{ fontSize: 10.5, color: T.faint, letterSpacing: "0.1em" }}>{p.id}</div>
                <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 13.5, marginTop: 4, color: p.chgPct >= 0 ? T.up : T.down }}>
                  {p.chgPct >= 0 ? "▲" : "▼"} {fmt(Math.abs(p.chgPct), 1)}%
                </div>
                <div style={{ height: 4, borderRadius: 2, background: T.line, marginTop: 7, overflow: "hidden" }}>
                  <div style={{ height: "100%", width: `${Math.min(Math.abs(p.chgPct) * 3.4, 100)}%`, background: p.chgPct >= 0 ? T.up : T.down }} />
                </div>
              </div>
            ))}
          </div>
        </div>

        <div style={{ padding: "16px 22px 0", display: "grid", gridTemplateColumns: "1fr 1fr", columnGap: 26 }}>
          <div>
            <Row k="Previous close" v={"$" + fmt(quote.prevClose)} />
            <Row k="52-week high" v={"$" + fmt(deep.hi52)} />
            <Row k="52-week low" v={"$" + fmt(deep.lo52)} />
            <Row k="Range position" v={fmt(deep.pos52, 0) + "% of 52-wk"} />
          </div>
          <div>
            <Row k="Market cap" v={"$" + def.mcap} />
            <Row k="P/E ratio" v={def.pe} />
            <Row k="EPS" v={"$" + deep.eps} />
            <Row k="Beta · Div yield" v={deep.beta + " · " + deep.divYield} />
          </div>
        </div>

        <div style={{ margin: "18px 22px 22px", background: T.panelSoft, border: `1px solid ${T.lineSoft}`, borderRadius: 9, padding: "15px 17px" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 10 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 9 }}>
              <span style={{ color: T.amber }}>✦</span>
              <span style={{ fontFamily: "'Fraunces', serif", fontWeight: 600, fontSize: 15 }}>AI report</span>
              <span style={{ fontSize: 11, color: T.faint }}>Buy now, wait, or skip it?</span>
            </div>
            {rep.status !== "done" && (
              <button className="run-btn" onClick={runReport} disabled={rep.status === "loading"}>
                {rep.status === "loading" ? "Analyzing…" : rep.status === "error" ? "Try again" : "Get the verdict"}
              </button>
            )}
          </div>

          {rep.status === "loading" && (
            <div style={{ marginTop: 13, display: "grid", gap: 8 }}>
              <div className="shimmer" style={{ height: 26, width: "40%", borderRadius: 6 }} />
              <div className="shimmer" style={{ height: 50, borderRadius: 6 }} />
            </div>
          )}
          {rep.status === "error" && (
            <p style={{ margin: "12px 0 0", color: T.down, fontSize: 13 }}>The report didn't come through — check your connection and try again.</p>
          )}
          {rep.status === "done" && (() => {
            const v = VERDICTS[rep.data.verdict] || VERDICTS.WAIT;
            return (
              <div className="sig-card" style={{ marginTop: 13 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 11, flexWrap: "wrap" }}>
                  <span style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 13, fontWeight: 600, letterSpacing: "0.08em",
                    color: v.color, background: v.bg, border: `1px solid ${v.border}`, padding: "5px 13px", borderRadius: 6 }}>
                    {v.label.toUpperCase()}
                  </span>
                  <span style={{ display: "inline-flex", gap: 3 }} aria-label={`Confidence ${rep.data.confidence} of 5`}>
                    {[1, 2, 3, 4, 5].map((d) => (
                      <span key={d} style={{ width: 15, height: 4, borderRadius: 2, background: d <= rep.data.confidence ? v.color : T.line }} />
                    ))}
                  </span>
                  <span style={{ fontSize: 13, color: T.text, fontWeight: 500 }}>{rep.data.headline}</span>
                </div>
                <p style={{ margin: "11px 0 0", fontSize: 13, color: T.text, lineHeight: 1.65 }}>{rep.data.reasoning}</p>
                <p style={{ margin: "9px 0 0", fontSize: 12, color: T.muted, lineHeight: 1.6 }}>
                  <span style={{ color: T.amber }}>What would change this:</span> {rep.data.watchFor}
                </p>
              </div>
            );
          })()}
          <p style={{ margin: "13px 0 0", fontSize: 10.5, color: T.faint, lineHeight: 1.5 }}>
            AI-generated verdict on simulated demo data — for illustration only, not investment advice.
          </p>
        </div>
      </div>
    </div>
  );
}

/* ── app ── */
export default function App() {
  const [selected, setSelected] = useState("NVDA");
  const [range, setRange] = useState("1D");
  const [tick, setTick] = useState(0);
  const [clock, setClock] = useState(new Date());
  const [watch, setWatch] = useState(STOCKS.map((s) => s.sym));
  const [detail, setDetail] = useState(null);
  const offsets = useRef(Object.fromEntries([...STOCKS, ...INDICES].map((s) => [s.sym, 0])));
  const flash = useRef({});
  const watchRef = useRef(watch);
  useEffect(() => { watchRef.current = watch; }, [watch]);

  const addStock = useCallback((sym) => setWatch((w) => (w.includes(sym) ? w : [...w, sym])), []);
  const removeStock = useCallback((sym) => {
    setWatch((w) => (w.length <= 1 ? w : w.filter((s) => s !== sym)));
    setSelected((sel) => {
      if (sel !== sym) return sel;
      const remaining = watchRef.current.filter((s) => s !== sym);
      return remaining[0] || sel;
    });
  }, []);

  useEffect(() => {
    const id = setInterval(() => {
      const syms = [...new Set([...watchRef.current, ...HOLDINGS.map((h) => h.sym)])];
      const all = [...syms.map((sym) => BY_SYM[sym]), ...INDICES];
      all.forEach((s) => {
        const step = (Math.random() - 0.494) * s.vol * 0.10;
        const prev = offsets.current[s.sym] || 0;
        offsets.current[s.sym] = Math.max(-0.06, Math.min(0.06, prev + step));
        flash.current[s.sym] = offsets.current[s.sym] > prev ? "up" : "down";
      });
      setTick((t) => t + 1);
    }, 1600);
    return () => clearInterval(id);
  }, []);
  useEffect(() => { const id = setInterval(() => setClock(new Date()), 1000); return () => clearInterval(id); }, []);

  const quotes = useMemo(() => {
    const make = (s) => {
      const dayRnd = mulberry32(hashStr(s.sym + "day"));
      const dayMove = (dayRnd() - 0.45) * s.vol * 2.4;
      const price = s.base * (1 + dayMove + (offsets.current[s.sym] || 0));
      const prevClose = s.base * (1 - s.vol * 0.3 * (dayRnd() - 0.5));
      const chg = price - prevClose, chgPct = (chg / prevClose) * 100;
      return { ...s, price, prevClose, chg, chgPct, dir: flash.current[s.sym] };
    };
    return {
      stocks: watch.map((sym) => make(BY_SYM[sym])),
      holdings: HOLDINGS.map((h) => make(BY_SYM[h.sym])),
      indices: INDICES.map(make),
    };
  }, [tick, watch]);

  const sel = quotes.stocks.find((q) => q.sym === selected) || quotes.stocks[0];
  const rangeDef = RANGES.find((r) => r.id === range);

  const baseSeries = useMemo(
    () => genSeries(sel.sym, range, rangeDef.pts, sel.prevClose, sel.vol),
    [sel.sym, range] // eslint-disable-line
  );
  const series = useMemo(() => {
    const s = [...baseSeries]; s[s.length - 1] = sel.price;
    if (range === "1D") s[0] = sel.prevClose;
    return s;
  }, [baseSeries, sel.price, range]);
  const labels = useMemo(() => timeLabels(range, rangeDef.pts), [range, rangeDef.pts]);

  const rangeFirst = series[0];
  const rangeChg = sel.price - rangeFirst, rangeChgPct = (rangeChg / rangeFirst) * 100;
  const trendUp = rangeChg >= 0;
  const chartColor = trendUp ? T.up : T.down;

  const dayHigh = Math.max(...(range === "1D" ? series : series.slice(-20)));
  const dayLow = Math.min(...(range === "1D" ? series : series.slice(-20)));
  const openPx = range === "1D" ? series[1] : sel.prevClose * 1.001;
  const volShares = useMemo(() => {
    const r = mulberry32(hashStr(sel.sym + "vol"));
    return (8 + r() * 60).toFixed(1) + "M";
  }, [sel.sym]);

  const sparkData = useMemo(() => {
    const m = {};
    watch.forEach((sym) => { const s = BY_SYM[sym]; m[sym] = genSeries(sym, "1D", 40, s.base, s.vol); });
    return m;
  }, [watch]);

  const timeStr = clock.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit", second: "2-digit" });
  const hr = clock.getHours();
  const marketOpen = hr >= 9 && hr < 16;

  return (
    <div style={{ minHeight: "100vh", background: T.ink, color: T.text, fontFamily: "'Space Grotesk', sans-serif" }}>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400;9..144,600&family=IBM+Plex+Mono:wght@400;500;600&family=Space+Grotesk:wght@400;500;600&display=swap');
        * { box-sizing: border-box; }
        body { margin: 0; }
        ::selection { background: ${T.amberSoft}; }
        .tape { overflow: hidden; border-top: 1px solid ${T.line}; border-bottom: 1px solid ${T.line}; background: ${T.panelSoft}; }
        .tape-inner { display: inline-flex; white-space: nowrap; animation: scroll 48s linear infinite; padding: 9px 0; }
        .tape:hover .tape-inner { animation-play-state: paused; }
        .tape-item { font-family: 'IBM Plex Mono', monospace; font-size: 12.5px; padding: 0 26px; border-right: 1px solid ${T.lineSoft}; }
        @keyframes scroll { from { transform: translateX(0); } to { transform: translateX(-50%); } }
        @media (prefers-reduced-motion: reduce) { .tape-inner { animation: none; } }
        .row { display: grid; grid-template-columns: minmax(0,1fr) 340px; gap: 14px; }
        @media (max-width: 940px) { .row { grid-template-columns: 1fr; } }
        .idx-strip { display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px; }
        @media (max-width: 760px) { .idx-strip { grid-template-columns: repeat(2, 1fr); } }
        .stats { display: grid; grid-template-columns: repeat(6, 1fr); gap: 0; }
        @media (max-width: 760px) { .stats { grid-template-columns: repeat(3, 1fr); row-gap: 14px; } }
        .wl-row { display: grid; grid-template-columns: 1fr auto auto auto; align-items: center; gap: 10px;
          width: 100%; text-align: left; padding: 11px 14px; background: transparent;
          border-bottom: 1px solid ${T.lineSoft}; cursor: pointer; color: inherit; font-family: inherit; user-select: none; }
        .wl-row:hover { background: rgba(255,255,255,0.025); }
        .wl-row:focus-visible, .rng:focus-visible { outline: 2px solid ${T.amber}; outline-offset: -2px; }
        .wl-row[data-active="true"] { background: ${T.amberSoft}; border-left: 2px solid ${T.amber}; padding-left: 12px; }
        .wl-x, .wl-i { width: 24px; height: 24px; border-radius: 6px; border: 1px solid transparent; background: transparent;
          color: ${T.faint}; font-size: 14px; line-height: 1; cursor: pointer; padding: 0; display: inline-flex; align-items: center; justify-content: center; }
        .wl-i:hover, .wl-i:focus-visible { color: ${T.amber}; border-color: rgba(240,163,63,0.4); }
        .wl-x:hover, .wl-x:focus-visible { color: ${T.down}; border-color: rgba(255,107,107,0.35); }
        .wl-x:focus-visible, .wl-i:focus-visible { outline: 2px solid currentColor; outline-offset: 1px; }
        .scr-row:focus-visible { outline: 2px solid ${T.amber}; outline-offset: -2px; }
        .skip-link { position: absolute; left: 12px; top: -48px; z-index: 60; background: ${T.amber}; color: #1A1206;
          font-family: 'IBM Plex Mono', monospace; font-size: 12.5px; font-weight: 600; padding: 9px 14px; border-radius: 7px;
          text-decoration: none; transition: top 0.15s; }
        .skip-link:focus { top: 12px; }
        .link-btn { background: none; border: 0; padding: 0; color: ${T.amber}; font-family: inherit; font-size: 12px;
          cursor: pointer; text-decoration: underline; text-underline-offset: 3px; text-decoration-color: rgba(240,163,63,0.4); }
        .link-btn:hover { text-decoration-color: ${T.amber}; }
        .link-btn:focus-visible { outline: 2px solid ${T.amber}; outline-offset: 2px; border-radius: 3px; }
        .icon-btn { width: 34px; height: 34px; border-radius: 8px; border: 1px solid ${T.line}; background: ${T.panelSoft};
          color: ${T.muted}; font-size: 14px; cursor: pointer; flex: 0 0 auto; }
        .icon-btn:hover { color: ${T.text}; border-color: ${T.muted}; }
        .icon-btn:focus-visible { outline: 2px solid ${T.amber}; outline-offset: 2px; }
        .overlay { position: fixed; inset: 0; z-index: 50; background: rgba(5,8,14,0.72); backdrop-filter: blur(3px);
          display: flex; align-items: flex-start; justify-content: center; padding: 4vh 14px; overflow-y: auto;
          animation: fadein 0.2s ease; }
        @keyframes fadein { from { opacity: 0; } to { opacity: 1; } }
        .modal { width: 100%; max-width: 740px; background: ${T.panel}; border: 1px solid ${T.line}; border-radius: 13px;
          box-shadow: 0 26px 70px rgba(0,0,0,0.55); animation: rise 0.3s cubic-bezier(0.2,0.8,0.2,1); outline: none; }
        @media (max-width: 680px) { .overlay { padding: 0; align-items: stretch; } .modal { border-radius: 0; border-left: 0; border-right: 0; min-height: 100vh; } }
        @media (prefers-reduced-motion: reduce) { .overlay, .modal { animation: none; } }
        .scr-grid { display: grid; grid-template-columns: minmax(0,1.9fr) 1.1fr 0.9fr 0.8fr 0.9fr 0.6fr 96px; gap: 12px; align-items: center; }
        @media (max-width: 680px) { .scr-grid { grid-template-columns: minmax(0,1.6fr) 0.9fr 0.8fr 88px; } }
        .scr-row { border-bottom: 1px solid ${T.lineSoft}; }
        .scr-row:hover { background: rgba(255,255,255,0.02); }
        .chip-row { display: flex; gap: 7px; overflow-x: auto; padding-bottom: 3px; scrollbar-width: thin; scrollbar-color: ${T.line} transparent; }
        .chip { flex: 0 0 auto; font-family: 'IBM Plex Mono', monospace; font-size: 11px; padding: 5px 11px; border-radius: 99px;
          border: 1px solid ${T.line}; background: transparent; color: ${T.muted}; cursor: pointer; white-space: nowrap; }
        .chip:hover { color: ${T.text}; border-color: ${T.muted}; }
        .chip[data-on="true"] { background: ${T.amberSoft}; color: ${T.amber}; border-color: rgba(240,163,63,0.4); }
        .chip:focus-visible { outline: 2px solid ${T.amber}; outline-offset: 1px; }
        .sel { font-family: 'IBM Plex Mono', monospace; font-size: 12px; background: ${T.panelSoft}; color: ${T.text};
          border: 1px solid ${T.line}; border-radius: 8px; padding: 9px 11px; cursor: pointer; }
        .sel:focus { outline: none; border-color: rgba(240,163,63,0.5); }
        .add-btn { font-family: 'IBM Plex Mono', monospace; font-size: 11px; padding: 6px 0; width: 100%; border-radius: 6px;
          cursor: pointer; background: rgba(61,214,140,0.10); color: ${T.up}; border: 1px solid rgba(61,214,140,0.35); transition: filter 0.12s; }
        .add-btn:hover { filter: brightness(1.18); }
        .add-btn[data-in="true"] { background: rgba(255,107,107,0.08); color: ${T.down}; border-color: rgba(255,107,107,0.3); }
        .add-btn:focus-visible { outline: 2px solid currentColor; outline-offset: 1px; }
        .pg-btn { font-family: 'IBM Plex Mono', monospace; font-size: 11.5px; padding: 6px 12px; border-radius: 6px;
          background: ${T.panelSoft}; color: ${T.muted}; border: 1px solid ${T.line}; cursor: pointer; }
        .pg-btn:hover:not(:disabled) { color: ${T.text}; border-color: ${T.muted}; }
        .pg-btn:disabled { opacity: 0.4; cursor: default; }
        .pg-btn:focus-visible { outline: 2px solid ${T.amber}; outline-offset: 1px; }
        .rng { font-family: 'IBM Plex Mono', monospace; font-size: 12px; padding: 5px 12px; border-radius: 5px;
          border: 1px solid transparent; background: transparent; color: ${T.muted}; cursor: pointer; }
        .rng:hover { color: ${T.text}; }
        .rng[data-on="true"] { background: ${T.amberSoft}; color: ${T.amber}; border-color: rgba(240,163,63,0.35); }
        .roll-window { display: inline-block; overflow: hidden; height: 1.18em; width: 1ch; }
        .roll-col { display: flex; flex-direction: column; transition: transform 0.65s cubic-bezier(0.22, 0.9, 0.24, 1); will-change: transform; }
        .roll-digit { height: 1.18em; line-height: 1.18em; display: inline-block; text-align: center; min-width: 1ch; }
        .sig-card { animation: rise 0.45s cubic-bezier(0.2, 0.8, 0.2, 1) both; }
        @keyframes rise { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: translateY(0); } }
        .pf-grid { display: grid; grid-template-columns: 1.5fr 1fr 1.1fr 1.1fr 1.3fr; gap: 12px; align-items: center; }
        @media (max-width: 680px) { .pf-grid { grid-template-columns: 1.4fr 1.1fr 1.3fr; } .hide-sm { display: none; } }
        .pf-sum { display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; }
        @media (max-width: 680px) { .pf-sum { grid-template-columns: 1fr; } }
        .chat-box { display: flex; flex-direction: column; gap: 9px; max-height: 280px; overflow-y: auto;
          padding: 4px 2px; scrollbar-width: thin; scrollbar-color: ${T.line} transparent; }
        .bubble { max-width: 78%; padding: 9px 13px; border-radius: 11px; font-size: 13px; line-height: 1.6;
          animation: rise 0.3s ease both; white-space: pre-wrap; overflow-wrap: break-word; }
        .bubble-user { align-self: flex-end; background: ${T.amberSoft}; border: 1px solid rgba(240,163,63,0.3);
          color: ${T.text}; border-bottom-right-radius: 3px; }
        .bubble-ai { align-self: flex-start; background: ${T.panelSoft}; border: 1px solid ${T.lineSoft};
          color: ${T.text}; border-bottom-left-radius: 3px; }
        .chat-input { flex: 1; min-width: 0; background: ${T.panelSoft}; border: 1px solid ${T.line}; border-radius: 8px;
          color: ${T.text}; font-family: 'Space Grotesk', sans-serif; font-size: 13px; padding: 10px 13px; }
        .chat-input::placeholder { color: ${T.faint}; }
        .chat-input:focus { outline: none; border-color: rgba(240,163,63,0.5); }
        .typing { display: inline-flex; gap: 4px; padding: 12px 14px; }
        .typing span { width: 5px; height: 5px; border-radius: 99px; background: ${T.muted}; animation: blink 1.2s infinite; }
        .typing span:nth-child(2) { animation-delay: 0.2s; } .typing span:nth-child(3) { animation-delay: 0.4s; }
        @keyframes blink { 0%, 60%, 100% { opacity: 0.25; } 30% { opacity: 1; } }
        .shimmer { background: linear-gradient(100deg, ${T.panelSoft} 35%, #1A2336 50%, ${T.panelSoft} 65%);
          background-size: 220% 100%; animation: shim 1.4s ease infinite; }
        @keyframes shim { from { background-position: 130% 0; } to { background-position: -90% 0; } }
        .run-btn { font-family: 'IBM Plex Mono', monospace; font-size: 12.5px; padding: 8px 16px; border-radius: 7px;
          background: ${T.amber}; color: #1A1206; border: 0; cursor: pointer; font-weight: 600; transition: filter 0.15s, transform 0.15s; }
        .run-btn:hover:not(:disabled) { filter: brightness(1.08); transform: translateY(-1px); }
        .run-btn:disabled { opacity: 0.6; cursor: wait; }
        .run-btn:focus-visible { outline: 2px solid ${T.text}; outline-offset: 2px; }
        @media (prefers-reduced-motion: reduce) {
          .roll-col, .run-btn { transition: none; }
          .sig-card, .shimmer, .typing span { animation: none; }
        }
      `}</style>

      <a href="#main-content" className="skip-link">Skip to main content</a>
      <header style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", flexWrap: "wrap", gap: 10, padding: "20px 22px 16px", maxWidth: 1180, margin: "0 auto" }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 14, flexWrap: "wrap" }}>
          <h1 style={{ margin: 0, fontFamily: "'Fraunces', serif", fontWeight: 600, fontSize: 27, letterSpacing: "0.01em" }}>
            Meridian<span style={{ color: T.amber }}>.</span>
          </h1>
          <span style={{ fontSize: 12, color: T.muted, letterSpacing: "0.14em", textTransform: "uppercase" }}>Market Desk</span>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 16, fontFamily: "'IBM Plex Mono', monospace", fontSize: 12 }}>
          <span style={{ display: "inline-flex", alignItems: "center", gap: 7, color: marketOpen ? T.up : T.muted }}>
            <span style={{ width: 7, height: 7, borderRadius: 99, background: marketOpen ? T.up : T.faint, boxShadow: marketOpen ? `0 0 8px ${T.up}` : "none" }} />
            {marketOpen ? "Market open" : "After hours"}
          </span>
          <span style={{ color: T.muted }}>{timeStr} ET</span>
        </div>
      </header>

      <Tape quotes={[...quotes.indices, ...quotes.stocks]} />

      <main id="main-content" style={{ maxWidth: 1180, margin: "0 auto", padding: "18px 22px 30px", display: "grid", gap: 14 }}>
        <section className="idx-strip" aria-label="Major indices">
          {quotes.indices.map((q) => (
            <div key={q.sym} style={{ background: T.panel, border: `1px solid ${T.line}`, borderRadius: 9, padding: "12px 14px" }}>
              <div style={{ fontSize: 11, letterSpacing: "0.12em", color: T.muted, textTransform: "uppercase" }}>{q.sym}</div>
              <div style={{ display: "flex", alignItems: "baseline", gap: 9, marginTop: 5, fontFamily: "'IBM Plex Mono', monospace" }}>
                <span style={{ fontSize: 16.5, fontWeight: 500 }}>
                  <Roller value={fmt(q.price, q.price > 5000 ? 1 : 2)} />
                </span>
                <span style={{ fontSize: 12, color: q.chgPct >= 0 ? T.up : T.down }}>
                  {q.chgPct >= 0 ? "▲" : "▼"} {fmt(Math.abs(q.chgPct))}%
                </span>
              </div>
            </div>
          ))}
        </section>

        <div className="row">
          <section style={{ background: T.panel, border: `1px solid ${T.line}`, borderRadius: 11, padding: "20px 20px 12px", display: "flex", flexDirection: "column", minHeight: 470 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", flexWrap: "wrap", gap: 12 }}>
              <div>
                <div style={{ display: "flex", alignItems: "baseline", gap: 11 }}>
                  <h2 style={{ margin: 0, fontFamily: "'Fraunces', serif", fontWeight: 600, fontSize: 23 }}>{sel.name}</h2>
                  <span style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 12.5, color: T.amber, background: T.amberSoft, padding: "2px 8px", borderRadius: 4 }}>{sel.sym}</span>
                </div>
                <div style={{ fontSize: 12, color: T.muted, marginTop: 4, display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
                  <span>{sel.sector} · NASDAQ</span>
                  <button className="link-btn" onClick={() => setDetail({ sym: sel.sym, quote: sel })}>
                    Details &amp; AI report →
                  </button>
                </div>
              </div>
              <div style={{ textAlign: "right" }}>
                <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 29, fontWeight: 600 }}>
                  <Roller value={"$" + fmt(sel.price)} />
                </div>
                <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 13, color: trendUp ? T.up : T.down }}>
                  {fmtSign(rangeChg)} ({fmtSign(rangeChgPct)}%) <span style={{ color: T.faint }}>{rangeDef.span.toLowerCase()}</span>
                </div>
              </div>
            </div>

            <div style={{ display: "flex", gap: 4, margin: "16px 0 6px" }}>
              {RANGES.map((r) => (
                <button key={r.id} className="rng" data-on={r.id === range} onClick={() => setRange(r.id)}>{r.id}</button>
              ))}
            </div>

            <div style={{ flex: 1, minHeight: 280 }}>
              <MainChart data={series} labels={labels} color={chartColor} prevRef={range === "1D" ? sel.prevClose : null} />
            </div>

            <div className="stats" style={{ borderTop: `1px solid ${T.lineSoft}`, marginTop: 10, paddingTop: 13, paddingBottom: 6 }}>
              {[
                ["Open", "$" + fmt(openPx)],
                ["High", "$" + fmt(dayHigh)],
                ["Low", "$" + fmt(dayLow)],
                ["Volume", volShares],
                ["Mkt cap", "$" + sel.mcap],
                ["P/E", sel.pe],
              ].map(([k, v]) => (
                <div key={k}>
                  <div style={{ fontSize: 10.5, letterSpacing: "0.12em", textTransform: "uppercase", color: T.faint }}>{k}</div>
                  <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 13.5, marginTop: 3 }}>{v}</div>
                </div>
              ))}
            </div>
          </section>

          <aside style={{ background: T.panel, border: `1px solid ${T.line}`, borderRadius: 11, overflow: "hidden", alignSelf: "start" }}>
            <div style={{ padding: "14px 16px 11px", borderBottom: `1px solid ${T.line}`, display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
              <div>
                <h3 style={{ margin: 0, fontSize: 12, letterSpacing: "0.14em", textTransform: "uppercase", color: T.muted, fontWeight: 500 }}>Watchlist</h3>
                <div style={{ fontSize: 10.5, color: T.faint, marginTop: 3 }}>Tap a row to chart it · ⓘ for details</div>
              </div>
              <span style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 11, color: T.faint }}>{watch.length} symbols</span>
            </div>
            <div style={{ maxHeight: 520, overflowY: "auto" }}>
              {quotes.stocks.map((q) => (
                <div key={q.sym} className="wl-row" data-active={q.sym === selected} role="button" tabIndex={0}
                  aria-pressed={q.sym === selected}
                  onClick={() => setSelected(q.sym)}
                  onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setSelected(q.sym); } }}>
                  <div style={{ minWidth: 0 }}>
                    <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontWeight: 600, fontSize: 13.5 }}>{q.sym}</div>
                    <div style={{ fontSize: 11, color: T.muted, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{q.name}</div>
                  </div>
                  <Spark data={sparkData[q.sym]} color={q.chgPct >= 0 ? T.up : T.down} w={56} h={26} />
                  <div style={{ textAlign: "right", fontFamily: "'IBM Plex Mono', monospace" }}>
                    <div style={{ fontSize: 13 }}><Roller value={fmt(q.price)} /></div>
                    <div style={{ fontSize: 11.5, color: q.chgPct >= 0 ? T.up : T.down }}>
                      {q.chgPct >= 0 ? "▲" : "▼"} {fmt(Math.abs(q.chgPct))}%
                    </div>
                  </div>
                  <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
                    <button className="wl-i" aria-label={`Open details for ${q.sym}`} title="Details & AI report"
                      onClick={(e) => { e.stopPropagation(); setDetail({ sym: q.sym, quote: q }); }}>ⓘ</button>
                    <button className="wl-x" aria-label={`Remove ${q.sym} from watchlist`} title="Remove"
                      onClick={(e) => { e.stopPropagation(); removeStock(q.sym); }}>×</button>
                  </div>
                </div>
              ))}
            </div>
          </aside>
        </div>

        <ScreenerPanel watch={watch} onAdd={addStock} onRemove={removeStock}
          onOpen={(sym, quote) => setDetail({ sym, quote })} />

        <PortfolioPanel quotes={quotes.holdings} />

        <AnalystPanel quotes={quotes.stocks} holdings={HOLDINGS} />

        <footer style={{ display: "flex", justifyContent: "space-between", flexWrap: "wrap", gap: 8, color: T.faint, fontSize: 11.5, padding: "2px 4px" }}>
          <span>Prices shown are simulated for demonstration — not live market data or investment advice.</span>
          <span style={{ fontFamily: "'IBM Plex Mono', monospace" }}>Meridian Desk · v1.0</span>
        </footer>
      </main>

      {detail && (
        <StockModal
          sym={detail.sym}
          quote={quotes.stocks.find((q) => q.sym === detail.sym) || detail.quote}
          inWatch={watch.includes(detail.sym)}
          onAdd={addStock}
          onRemove={removeStock}
          onClose={() => setDetail(null)}
        />
      )}
    </div>
  );
}
