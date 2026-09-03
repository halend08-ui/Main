/** Shared presentational primitives. */

export function Badge({ children, tone = "neutral", title }) {
  return (
    <span className={`badge badge-${tone}`} title={title}>
      {children}
    </span>
  );
}

const REC_TONE = {
  BUY: "positive",
  HOLD: "neutral",
  WATCH: "info",
  SELL: "negative",
  AVOID: "negative",
  INSUFFICIENT_DATA: "muted",
};

export function RecommendationBadge({ value }) {
  if (!value) return <Badge tone="muted">n/a</Badge>;
  return (
    <Badge tone={REC_TONE[value] || "neutral"}>
      {value.replace(/_/g, " ")}
    </Badge>
  );
}

const RISK_TONE = {
  low: "positive",
  moderate: "neutral",
  elevated: "warning",
  high: "negative",
  extreme: "negative",
};

export function RiskBadge({ value }) {
  if (!value) return <Badge tone="muted">unknown</Badge>;
  return <Badge tone={RISK_TONE[value] || "neutral"}>{value}</Badge>;
}

const QUALITY_TONE = {
  excellent: "positive",
  good: "positive",
  fair: "warning",
  poor: "negative",
  insufficient: "negative",
};

export function QualityBadge({ value }) {
  if (!value) return <Badge tone="muted">unknown</Badge>;
  return (
    <Badge
      tone={QUALITY_TONE[value] || "neutral"}
      title="Data quality caps how much confidence any conclusion deserves"
    >
      {value}
    </Badge>
  );
}

export function Loading({ what = "data" }) {
  return <div className="state">Loading {what}…</div>;
}

export function ErrorState({ error, onRetry }) {
  return (
    <div className="state state-error">
      <strong>Could not load this view.</strong>
      <p>{error?.message || String(error)}</p>
      {onRetry && (
        <button type="button" onClick={onRetry}>
          Retry
        </button>
      )}
    </div>
  );
}

export function EmptyState({ children }) {
  return <div className="state state-empty">{children}</div>;
}

export function Section({ title, subtitle, children, actions }) {
  return (
    <section className="section">
      <header className="section-head">
        <div>
          <h2>{title}</h2>
          {subtitle && <p className="subtitle">{subtitle}</p>}
        </div>
        {actions}
      </header>
      {children}
    </section>
  );
}

export function Stat({ label, value, hint, tone }) {
  return (
    <div className="stat">
      <span className="stat-label">{label}</span>
      <span className={`stat-value ${tone ? `tone-${tone}` : ""}`}>{value}</span>
      {hint && <span className="stat-hint">{hint}</span>}
    </div>
  );
}

/**
 * A dependency-free line chart. Deliberately plain: this is a research tool,
 * and the point is to read the series, not to admire the chart.
 */
export function LineChart({ series, height = 220, showZero = false }) {
  const points = (series || []).filter((s) => s.values?.some((v) => v != null));
  if (!points.length) return <EmptyState>No series to plot.</EmptyState>;

  const all = points.flatMap((s) => s.values.filter((v) => v != null));
  let min = Math.min(...all);
  let max = Math.max(...all);
  if (showZero) min = Math.min(min, 0);
  if (min === max) {
    min -= 1;
    max += 1;
  }
  const pad = (max - min) * 0.06;
  min -= pad;
  max += pad;
  const width = 900;
  const length = Math.max(...points.map((s) => s.values.length));
  const x = (i) => (i / Math.max(length - 1, 1)) * width;
  const y = (v) => height - ((v - min) / (max - min)) * height;

  return (
    <svg className="chart" viewBox={`0 0 ${width} ${height}`} role="img"
         preserveAspectRatio="none">
      {[0, 0.25, 0.5, 0.75, 1].map((t) => (
        <line key={t} x1={0} x2={width} y1={height * t} y2={height * t}
              className="chart-grid" />
      ))}
      {points.map((s) => {
        let path = "";
        let started = false;
        s.values.forEach((v, i) => {
          if (v == null) {
            started = false;
            return;
          }
          path += `${started ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)} `;
          started = true;
        });
        return (
          <path key={s.label} d={path.trim()} fill="none"
                className={`chart-line chart-${s.tone || "primary"}`} />
        );
      })}
    </svg>
  );
}

export function Legend({ series }) {
  return (
    <div className="legend">
      {series.map((s) => (
        <span key={s.label} className={`legend-item legend-${s.tone || "primary"}`}>
          {s.label}
        </span>
      ))}
    </div>
  );
}
