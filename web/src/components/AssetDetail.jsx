import { useEffect, useState } from "react";
import { api, fmt } from "../api";
import {
  Badge, EmptyState, ErrorState, Legend, LineChart, Loading, QualityBadge,
  RecommendationBadge, RiskBadge, Section, Stat,
} from "./Primitives";

export default function AssetDetail({ symbol, onBack }) {
  const [state, setState] = useState({ status: "loading" });
  const [memo, setMemo] = useState(null);

  useEffect(() => {
    let cancelled = false;
    setState({ status: "loading" });
    setMemo(null);
    Promise.all([api.asset(symbol), api.prices(symbol, 750)])
      .then(([detail, prices]) => {
        if (!cancelled) setState({ status: "ready", detail, prices });
      })
      .catch((error) => !cancelled && setState({ status: "error", error }));
    return () => {
      cancelled = true;
    };
  }, [symbol]);

  if (state.status === "loading") return <Loading what={`${symbol} research`} />;
  if (state.status === "error") return <ErrorState error={state.error} />;

  const { asset, recommendation, score_history: scoreHistory, news,
          data_quality: quality, recommendation_history: recHistory } = state.detail;
  const bars = state.prices.bars || [];
  const indicators = state.prices.indicators || {};

  const priceSeries = [
    { label: "Adjusted close", tone: "primary",
      values: bars.map((b) => b.adj_close ?? b.close) },
  ];
  if (indicators.sma_200) {
    priceSeries.push({
      label: "200-day average", tone: "muted",
      values: indicators.sma_200.slice(-bars.length),
    });
  }

  return (
    <>
      <button type="button" className="back" onClick={onBack}>
        ← back
      </button>

      <Section
        title={`${asset.symbol} — ${asset.name || "unnamed"}`}
        subtitle={[asset.asset_class, asset.sector, asset.exchange]
          .filter(Boolean)
          .join(" · ")}
      >
        {!asset.is_active && (
          <div className="callout callout-warning">
            This asset is inactive
            {asset.delisted_date ? ` (delisted ${asset.delisted_date})` : ""}. It
            is retained so historical analysis is not survivorship-biased.
          </div>
        )}
        {recommendation ? (
          <>
            <div className="stat-row">
              <Stat label="Recommendation"
                    value={<RecommendationBadge value={recommendation.recommendation} />} />
              <Stat label="Score" value={fmt.num(recommendation.score, 0)} hint="of 100" />
              <Stat label="Confidence" value={fmt.pct(recommendation.confidence, 0)}
                    hint="in the estimate, not the outcome" />
              <Stat label="Price" value={fmt.money(recommendation.price)} />
              <Stat label="Risk" value={<RiskBadge value={recommendation.risk_level} />} />
              <Stat label="Data quality"
                    value={<QualityBadge value={recommendation.data_quality} />} />
              <Stat label="P(positive)"
                    value={fmt.pct(recommendation.probability_positive, 0)}
                    hint={`over ${recommendation.horizon}`} />
            </div>

            <div className="scenario-grid">
              {["bear", "base", "bull"].map((scenario) => (
                <div key={scenario} className={`scenario scenario-${scenario}`}>
                  <span className="scenario-label">{scenario}</span>
                  <span className="scenario-value">
                    {fmt.money(recommendation.fair_value?.[scenario])}
                  </span>
                  <span className="scenario-return">
                    {fmt.pct(recommendation.expected_return?.[scenario], 0)}
                  </span>
                </div>
              ))}
            </div>
          </>
        ) : (
          <EmptyState>
            This asset has not been analysed yet, or the analysis produced
            insufficient reliable data.
          </EmptyState>
        )}
      </Section>

      <Section title="Price history" subtitle={`${bars.length} sessions stored`}>
        {bars.length === 0 ? (
          <EmptyState>No price history stored for this asset.</EmptyState>
        ) : (
          <>
            <LineChart series={priceSeries} height={260} />
            <Legend series={priceSeries} />
          </>
        )}
      </Section>

      {recommendation && (
        <>
          <Section title="Why" subtitle="Strongest evidence, both directions">
            <div className="evidence-grid">
              <div>
                <h3>Supporting</h3>
                <ul className="evidence">
                  {(recommendation.evidence || [])
                    .filter((e) => e.direction > 0.1)
                    .sort((a, b) => b.direction * b.weight - a.direction * a.weight)
                    .slice(0, 6)
                    .map((e, i) => (
                      <li key={i}>
                        <strong>{e.label}</strong>
                        <span>{e.detail}</span>
                        <Badge tone="muted">{e.claim_type?.replace(/_/g, " ")}</Badge>
                      </li>
                    ))}
                </ul>
              </div>
              <div>
                <h3>Against</h3>
                <ul className="evidence">
                  {(recommendation.evidence || [])
                    .filter((e) => e.direction < -0.1)
                    .sort((a, b) => a.direction * a.weight - b.direction * b.weight)
                    .slice(0, 6)
                    .map((e, i) => (
                      <li key={i}>
                        <strong>{e.label}</strong>
                        <span>{e.detail}</span>
                        <Badge tone="muted">{e.claim_type?.replace(/_/g, " ")}</Badge>
                      </li>
                    ))}
                </ul>
              </div>
            </div>
          </Section>

          <Section title="Bear case" subtitle="Constructed from the actual negative evidence">
            <p className="prose">{recommendation.bear_case || "Not constructed."}</p>
            <h3>Bull case</h3>
            <p className="prose">{recommendation.bull_case || "Not constructed."}</p>
          </Section>

          <Section title="Sell / exit conditions"
                   subtitle="Measurable conditions, defined before the position exists">
            <ul className="conditions">
              {(recommendation.sell_conditions || []).map((c, i) => (
                <li key={i}>
                  <Badge tone={c.severity === "sell" ? "negative" : "warning"}>
                    {c.severity}
                  </Badge>
                  {c.description}
                </li>
              ))}
            </ul>
            <h3>What would make this wrong</h3>
            <ul className="conditions">
              {(recommendation.invalidation || []).map((item, i) => (
                <li key={i}>{item}</li>
              ))}
            </ul>
            {(recommendation.gates_failed || []).length > 0 && (
              <div className="callout">
                <strong>Why the system will not go further:</strong>
                <ul>
                  {recommendation.gates_failed.map((gate) => (
                    <li key={gate}>{gate}</li>
                  ))}
                </ul>
              </div>
            )}
          </Section>
        </>
      )}

      <Section title="Score history">
        {scoreHistory?.length ? (
          <LineChart
            series={[{ label: "score", tone: "primary",
                       values: scoreHistory.map((s) => s.total_score) }]}
            height={160}
          />
        ) : (
          <EmptyState>Only one analysis so far — no history to plot.</EmptyState>
        )}
        {recHistory?.length > 1 && (
          <table className="table compact">
            <thead>
              <tr><th>Date</th><th>Rec</th><th className="num">Score</th>
                  <th className="num">Price</th><th>Model</th></tr>
            </thead>
            <tbody>
              {recHistory.slice(-10).reverse().map((row) => (
                <tr key={`${row.as_of}-${row.model_version}`}>
                  <td>{fmt.date(row.as_of)}</td>
                  <td><RecommendationBadge value={row.recommendation} /></td>
                  <td className="num">{fmt.num(row.score, 0)}</td>
                  <td className="num">{fmt.money(row.price)}</td>
                  <td className="muted">{row.model_version}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Section>

      <Section title="Data quality">
        {quality ? (
          <>
            <div className="stat-row">
              <Stat label="Grade" value={<QualityBadge value={quality.grade} />} />
              <Stat label="Score" value={fmt.num(quality.score, 2)} />
              <Stat label="Checked" value={fmt.date(quality.checked_at)} />
            </div>
            <ul className="issues">
              {(quality.issues || []).map((issue, i) => (
                <li key={i}>
                  <Badge tone={issue.severity === "FATAL" ? "negative" : "warning"}>
                    {issue.severity}
                  </Badge>
                  {issue.message}
                </li>
              ))}
            </ul>
          </>
        ) : (
          <EmptyState>No data-quality report stored for this asset.</EmptyState>
        )}
      </Section>

      <Section title="News" subtitle="Weighted by source tier; sentiment is weak evidence">
        {news?.length ? (
          <ul className="news">
            {news.map((item, i) => (
              <li key={i}>
                <span className="muted">{fmt.date(item.published_at)}</span>
                {item.url ? (
                  <a href={item.url} target="_blank" rel="noreferrer">
                    {item.headline}
                  </a>
                ) : (
                  <span>{item.headline}</span>
                )}
                <Badge tone="muted">{item.source_tier?.replace(/_/g, " ")}</Badge>
              </li>
            ))}
          </ul>
        ) : (
          <EmptyState>
            No news retrieved. Event and sentiment analysis are therefore absent
            rather than neutral.
          </EmptyState>
        )}
      </Section>

      <Section
        title="Investment memo"
        actions={
          <button type="button" onClick={() =>
            api.memo(symbol).then(setMemo).catch((e) => setMemo({ error: e.message }))}>
            Load memo
          </button>
        }
      >
        {memo?.error && <EmptyState>{memo.error}</EmptyState>}
        {memo?.markdown && <pre className="memo">{memo.markdown}</pre>}
        {!memo && (
          <p className="muted">
            Memos are generated for high-ranked opportunities during the daily run.
          </p>
        )}
      </Section>
    </>
  );
}
