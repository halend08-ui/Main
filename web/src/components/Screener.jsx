import { useEffect, useState } from "react";
import { api, fmt } from "../api";
import {
  EmptyState, ErrorState, Loading, QualityBadge, RecommendationBadge,
  RiskBadge, Section,
} from "./Primitives";

const DEFAULT_FILTERS = {
  min_score: 0,
  min_confidence: 0,
  max_risk: "",
  sector: "",
  asset_class: "",
  min_market_cap: "",
};

export default function Screener({ onSelectAsset }) {
  const [filters, setFilters] = useState(DEFAULT_FILTERS);
  const [state, setState] = useState({ status: "loading" });

  useEffect(() => {
    let cancelled = false;
    setState((prev) => ({ ...prev, status: "loading" }));
    api
      .screen({ ...filters, limit: 200 })
      .then((data) => !cancelled && setState({ status: "ready", data }))
      .catch((error) => !cancelled && setState({ status: "error", error }));
    return () => {
      cancelled = true;
    };
  }, [filters]);

  const update = (key) => (event) =>
    setFilters((prev) => ({ ...prev, [key]: event.target.value }));

  return (
    <Section
      title="Screener"
      subtitle="Filter the analysed universe. Assets with insufficient data are
                excluded rather than scored optimistically."
      actions={
        <button type="button" onClick={() => setFilters(DEFAULT_FILTERS)}>
          Reset
        </button>
      }
    >
      <div className="filters">
        <label>
          Min score
          <input type="number" min="0" max="100" value={filters.min_score}
                 onChange={update("min_score")} />
        </label>
        <label>
          Min confidence
          <input type="number" min="0" max="1" step="0.05"
                 value={filters.min_confidence} onChange={update("min_confidence")} />
        </label>
        <label>
          Max risk
          <select value={filters.max_risk} onChange={update("max_risk")}>
            <option value="">any</option>
            <option value="low">low</option>
            <option value="moderate">moderate</option>
            <option value="elevated">elevated</option>
            <option value="high">high</option>
          </select>
        </label>
        <label>
          Asset class
          <select value={filters.asset_class} onChange={update("asset_class")}>
            <option value="">any</option>
            <option value="equity">equity</option>
            <option value="crypto">crypto</option>
          </select>
        </label>
        <label>
          Sector
          <input type="text" placeholder="any" value={filters.sector}
                 onChange={update("sector")} />
        </label>
        <label>
          Min market cap (USD)
          <input type="number" placeholder="any" value={filters.min_market_cap}
                 onChange={update("min_market_cap")} />
        </label>
      </div>

      {state.status === "loading" && <Loading what="screen results" />}
      {state.status === "error" && <ErrorState error={state.error} />}
      {state.status === "ready" && (
        state.data.items.length === 0 ? (
          <EmptyState>
            Nothing matches these filters in the latest analysis.
          </EmptyState>
        ) : (
          <>
            <p className="muted">
              {state.data.total_matching} matching asset(s) as of{" "}
              {fmt.date(state.data.as_of)}
            </p>
            <table className="table">
              <thead>
                <tr>
                  <th>Asset</th>
                  <th>Class</th>
                  <th>Sector</th>
                  <th>Rec</th>
                  <th className="num">Score</th>
                  <th className="num">Conf.</th>
                  <th className="num">Base return</th>
                  <th>Risk</th>
                  <th>Data</th>
                </tr>
              </thead>
              <tbody>
                {state.data.items.map((item) => (
                  <tr key={item.symbol} className="row-clickable"
                      onClick={() => onSelectAsset(item.symbol)}>
                    <td><strong>{item.symbol}</strong></td>
                    <td>{item.asset_class}</td>
                    <td>{item.sector || "—"}</td>
                    <td><RecommendationBadge value={item.recommendation} /></td>
                    <td className="num">{fmt.num(item.score, 0)}</td>
                    <td className="num">{fmt.pct(item.confidence, 0)}</td>
                    <td className="num">{fmt.pct(item.expected_return?.base, 0)}</td>
                    <td><RiskBadge value={item.risk_level} /></td>
                    <td><QualityBadge value={item.data_quality} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )
      )}
    </Section>
  );
}
