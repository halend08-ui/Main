import { useEffect, useState } from "react";
import { api, fmt } from "../api";
import {
  EmptyState, ErrorState, Loading, QualityBadge, RecommendationBadge,
  RiskBadge, Section, Stat,
} from "./Primitives";

export default function Overview({ onSelectAsset }) {
  const [state, setState] = useState({ status: "loading" });

  useEffect(() => {
    let cancelled = false;
    Promise.all([api.market(), api.opportunities({ limit: 15 }), api.alerts({ limit: 8 })])
      .then(([market, opportunities, alerts]) => {
        if (!cancelled) setState({ status: "ready", market, opportunities, alerts });
      })
      .catch((error) => !cancelled && setState({ status: "error", error }));
    return () => {
      cancelled = true;
    };
  }, []);

  if (state.status === "loading") return <Loading what="market overview" />;
  if (state.status === "error") return <ErrorState error={state.error} />;

  const regime = state.market.market?.regime || {};
  const macro = state.market.market?.macro_stance || {};
  const risks = state.market.market?.major_risks || [];
  const items = state.opportunities.items || [];

  return (
    <>
      <Section
        title="Market Overview"
        subtitle={`Analysis as of ${fmt.date(state.market.as_of)}`}
      >
        <div className="stat-row">
          <Stat label="Regime" value={regime.regime || "unknown"}
                hint={`confidence ${fmt.pct(regime.confidence, 0)}`} />
          <Stat label="Volatility" value={regime.volatility_regime || "unknown"} />
          <Stat label="Risk appetite" value={regime.risk_appetite || "unknown"} />
          <Stat label="Policy stance" value={macro.policy || "unknown"} />
          <Stat label="Yield curve" value={macro.yield_curve || "unknown"} />
          <Stat label="Credit" value={macro.credit || "unknown"} />
        </div>
        {risks.length > 0 && (
          <ul className="risk-list">
            {risks.map((risk) => (
              <li key={risk}>{risk}</li>
            ))}
          </ul>
        )}
        {state.market.warnings?.length > 0 && (
          <div className="callout callout-warning">
            <strong>Run warnings:</strong>
            <ul>
              {state.market.warnings.map((w) => (
                <li key={w}>{w}</li>
              ))}
            </ul>
          </div>
        )}
      </Section>

      <Section
        title="Top Opportunities"
        subtitle="Ranked by composite score. Every figure is a model estimate."
      >
        {items.length === 0 ? (
          <EmptyState>
            No asset cleared the minimum evidence and quality bars in the latest
            run. That is a legitimate result, not a failure.
          </EmptyState>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>Asset</th>
                <th>Rec</th>
                <th className="num">Score</th>
                <th className="num">Conf.</th>
                <th className="num">Price</th>
                <th className="num">Base FV</th>
                <th className="num">Base return</th>
                <th className="num">P(up)</th>
                <th>Risk</th>
                <th>Data</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr key={item.symbol} onClick={() => onSelectAsset(item.symbol)}
                    className="row-clickable">
                  <td>
                    <strong>{item.symbol}</strong>
                    <span className="muted"> {item.sector || ""}</span>
                  </td>
                  <td><RecommendationBadge value={item.recommendation} /></td>
                  <td className="num">{fmt.num(item.score, 0)}</td>
                  <td className="num">{fmt.pct(item.confidence, 0)}</td>
                  <td className="num">{fmt.money(item.price)}</td>
                  <td className="num">{fmt.money(item.fair_value?.base)}</td>
                  <td className={`num ${
                    (item.expected_return?.base ?? 0) >= 0 ? "tone-positive" : "tone-negative"
                  }`}>
                    {fmt.pct(item.expected_return?.base, 0)}
                  </td>
                  <td className="num">{fmt.pct(item.probability_positive, 0)}</td>
                  <td><RiskBadge value={item.risk_level} /></td>
                  <td><QualityBadge value={item.data_quality} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Section>

      <Section title="Recent Alerts" subtitle="Threshold-driven, deduplicated daily">
        {(state.alerts || []).length === 0 ? (
          <EmptyState>No alerts in the last seven days.</EmptyState>
        ) : (
          <ul className="alert-list">
            {state.alerts.map((alert) => (
              <li key={alert.id} className={`alert alert-${alert.severity}`}>
                <span className="alert-kind">{alert.kind}</span>
                <strong>{alert.title}</strong>
                <span className="muted">{alert.detail}</span>
              </li>
            ))}
          </ul>
        )}
      </Section>
    </>
  );
}
