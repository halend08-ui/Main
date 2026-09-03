import { useEffect, useState } from "react";
import { api, fmt } from "../api";
import { EmptyState, ErrorState, Loading, Section, Stat } from "./Primitives";

export default function Portfolio({ onSelectAsset }) {
  const [state, setState] = useState({ status: "loading" });

  useEffect(() => {
    let cancelled = false;
    api
      .portfolio()
      .then((data) => !cancelled && setState({ status: "ready", data }))
      .catch((error) => !cancelled && setState({ status: "error", error }));
    return () => {
      cancelled = true;
    };
  }, []);

  if (state.status === "loading") return <Loading what="portfolio" />;
  if (state.status === "error") return <ErrorState error={state.error} />;

  const { positions = [], risk = {}, breaches = [], note } = state.data;

  return (
    <Section title="Hypothetical Portfolio" subtitle={note}>
      {positions.length === 0 ? (
        <EmptyState>No positions are open.</EmptyState>
      ) : (
        <>
          <div className="stat-row">
            <Stat label="Positions" value={positions.length} />
            <Stat label="Volatility" value={fmt.pct(risk.volatility, 1)}
                  hint={`measured on ${risk.holdings_measured ?? 0} holdings`} />
            <Stat label="Concentration (HHI)" value={fmt.num(risk.hhi, 3)}
                  hint={`≈ ${fmt.num(risk.effective_positions, 1)} equal positions`} />
            <Stat label="VaR 95%" value={fmt.pct(risk.var_95, 1)} />
            <Stat label="Expected shortfall" value={fmt.pct(risk.expected_shortfall_975, 1)} />
          </div>
          {breaches.length > 0 && (
            <div className="callout callout-warning">
              <strong>Limit breaches</strong>
              <ul>
                {breaches.map((breach) => (
                  <li key={breach}>{breach}</li>
                ))}
              </ul>
            </div>
          )}
          <table className="table">
            <thead>
              <tr>
                <th>Asset</th><th className="num">Entry</th><th className="num">Price</th>
                <th className="num">Return</th><th className="num">Quantity</th>
                <th>Thesis</th>
              </tr>
            </thead>
            <tbody>
              {positions.map((p) => {
                const ret = p.price && p.entry_price ? p.price / p.entry_price - 1 : null;
                return (
                  <tr key={p.id} className="row-clickable"
                      onClick={() => onSelectAsset(p.symbol)}>
                    <td><strong>{p.symbol}</strong></td>
                    <td className="num">{fmt.money(p.entry_price)}</td>
                    <td className="num">{fmt.money(p.price)}</td>
                    <td className={`num ${ret >= 0 ? "tone-positive" : "tone-negative"}`}>
                      {fmt.pct(ret, 1)}
                    </td>
                    <td className="num">{fmt.num(p.quantity, 2)}</td>
                    <td className="muted">{p.thesis || "—"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </>
      )}
    </Section>
  );
}
