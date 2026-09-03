import { useEffect, useState } from "react";
import { api, fmt } from "../api";
import {
  EmptyState, ErrorState, Loading, Section, Stat,
} from "./Primitives";

export default function Performance() {
  const [state, setState] = useState({ status: "loading" });

  useEffect(() => {
    let cancelled = false;
    api
      .performance()
      .then((data) => !cancelled && setState({ status: "ready", data }))
      .catch((error) => !cancelled && setState({ status: "error", error }));
    return () => {
      cancelled = true;
    };
  }, []);

  if (state.status === "loading") return <Loading what="model performance" />;
  if (state.status === "error") return <ErrorState error={state.error} />;

  const summary = state.data.summary || {};
  const overall = summary.overall || {};
  const buckets = state.data.buckets || [];
  const calibration = state.data.calibration || [];
  const weak = summary.weak_buckets || [];
  const confidenceCheck = summary.confidence_check || {};

  return (
    <>
      <Section title="Model Performance" subtitle={state.data.caveat}>
        {overall.sufficient === false || !overall.samples ? (
          <EmptyState>
            {overall.note ||
              "Not enough evaluated predictions yet. " +
                `${state.data.open_predictions} prediction(s) are awaiting their horizon.`}
          </EmptyState>
        ) : (
          <div className="stat-row">
            <Stat label="Evaluated" value={overall.samples} />
            <Stat label="Hit rate" value={fmt.pct(overall.hit_rate, 1)} />
            <Stat label="Avg return" value={fmt.pct(overall.avg_return, 1)} />
            <Stat label="Avg excess" value={fmt.pct(overall.avg_excess, 1)}
                  hint="versus benchmark" />
            <Stat label="Brier skill" value={fmt.num(overall.brier_skill, 3)}
                  hint="positive beats the base rate"
                  tone={(overall.brier_skill ?? 0) > 0 ? "positive" : "negative"} />
            <Stat label="Calibration error"
                  value={fmt.pct(overall.calibration_error, 1)} />
          </div>
        )}
      </Section>

      {confidenceCheck.assessable && (
        <Section title="Is confidence informative?" subtitle={confidenceCheck.verdict}>
          <table className="table compact">
            <thead>
              <tr><th>Stated confidence</th><th className="num">Hit rate</th>
                  <th className="num">Samples</th></tr>
            </thead>
            <tbody>
              {(confidenceCheck.buckets || []).map((row) => (
                <tr key={row.confidence}>
                  <td>{row.confidence}</td>
                  <td className="num">{fmt.pct(row.hit_rate, 1)}</td>
                  <td className="num">{row.samples}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Section>
      )}

      {weak.length > 0 && (
        <Section title="Where the model is systematically wrong">
          <ul className="issues">
            {weak.map((finding, i) => (
              <li key={i}>
                <strong>{finding.issue}</strong> — {finding.detail}
              </li>
            ))}
          </ul>
        </Section>
      )}

      <Section title="Performance by bucket"
               subtitle="Buckets below the sample floor report insufficient rather
                         than a misleading number">
        {buckets.length === 0 ? (
          <EmptyState>No bucketed performance recorded yet.</EmptyState>
        ) : (
          <table className="table compact">
            <thead>
              <tr>
                <th>Bucket</th><th>Value</th><th className="num">Samples</th>
                <th className="num">Hit rate</th><th className="num">Avg return</th>
                <th className="num">Brier</th>
              </tr>
            </thead>
            <tbody>
              {buckets.map((row, i) => (
                <tr key={i}>
                  <td>{row.bucket_kind}</td>
                  <td>{row.bucket_value}</td>
                  <td className="num">{row.samples}</td>
                  <td className="num">{fmt.pct(row.hit_rate, 1)}</td>
                  <td className="num">{fmt.pct(row.avg_return, 1)}</td>
                  <td className="num">{fmt.num(row.brier, 3)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Section>

      {calibration.length > 0 && (
        <Section title="Calibration"
                 subtitle="A 70% prediction should be right about 70% of the time">
          <table className="table compact">
            <thead>
              <tr><th className="num">Predicted</th><th className="num">Observed</th>
                  <th className="num">Samples</th></tr>
            </thead>
            <tbody>
              {calibration.map((row, i) => (
                <tr key={i}>
                  <td className="num">{fmt.pct(row.predicted_mean, 0)}</td>
                  <td className="num">{fmt.pct(row.observed_rate, 0)}</td>
                  <td className="num">{row.samples}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Section>
      )}
    </>
  );
}
