import { useEffect, useState } from "react";
import { api, fmt } from "../api";
import { EmptyState, ErrorState, Loading, Section } from "./Primitives";

export default function ResearchQueue({ onSelectAsset }) {
  const [state, setState] = useState({ status: "loading" });

  useEffect(() => {
    let cancelled = false;
    Promise.all([api.queue(100), api.report("daily").catch(() => null)])
      .then(([queue, report]) => {
        if (!cancelled) setState({ status: "ready", queue, report });
      })
      .catch((error) => !cancelled && setState({ status: "error", error }));
    return () => {
      cancelled = true;
    };
  }, []);

  if (state.status === "loading") return <Loading what="research queue" />;
  if (state.status === "error") return <ErrorState error={state.error} />;

  const discoveries = state.report?.payload?.discoveries || [];

  return (
    <>
      <Section
        title="Research Queue"
        subtitle="Assets awaiting deeper analysis, ranked by research priority"
      >
        {state.queue.length === 0 ? (
          <EmptyState>The queue is empty.</EmptyState>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>Asset</th><th className="num">Priority</th><th className="num">Stage</th>
                <th>Reason</th><th>Trigger</th><th>Queued</th>
              </tr>
            </thead>
            <tbody>
              {state.queue.map((row) => (
                <tr key={row.id} className="row-clickable"
                    onClick={() => onSelectAsset(row.symbol)}>
                  <td><strong>{row.symbol}</strong></td>
                  <td className="num">{fmt.num(row.priority, 2)}</td>
                  <td className="num">{row.stage}</td>
                  <td>{row.reason}</td>
                  <td className="muted">{row.trigger || "—"}</td>
                  <td className="muted">{fmt.date(row.queued_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Section>

      <Section
        title="Latest discoveries"
        subtitle="Research candidates only — none of these is a recommendation"
      >
        {discoveries.length === 0 ? (
          <EmptyState>Nothing new met the discovery thresholds.</EmptyState>
        ) : (
          <ul className="discoveries">
            {discoveries.map((d) => (
              <li key={d.symbol}>
                <button type="button" onClick={() => onSelectAsset(d.symbol)}>
                  {d.symbol}
                </button>
                <span>{d.reason}</span>
                <span className="muted">[{d.trigger}]</span>
                {(d.warnings || []).map((w) => (
                  <span key={w} className="warning-note">{w}</span>
                ))}
              </li>
            ))}
          </ul>
        )}
      </Section>
    </>
  );
}
