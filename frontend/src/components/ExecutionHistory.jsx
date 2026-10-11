import { useEffect, useId, useState } from "react";
import Icon from "./Icon.jsx";
import { fetchExecutionHistory } from "../api/candidates.js";
import {
  STATUS_ORDER,
  countByStatus,
  executionStatus,
  formatDuration,
} from "../utils/executionHistory.js";

function formatDate(value) {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date.toLocaleString();
}

function ExecutionRecord({ record }) {
  const status = executionStatus(record.status);
  const started = formatDate(record.claimed_at);
  const duration = formatDuration(record.duration_seconds);
  const details = Array.isArray(record.details) ? record.details : [];

  return (
    <li className="execution-record">
      <div className="execution-record-header">
        <span className={`status-pill${status.tone}`}>{status.label}</span>
        <code className="mono execution-event-id">{record.source_event_id}</code>
      </div>
      <p className="candidate-muted execution-record-meta">
        {started ? `Started ${started}` : "Start time unavailable"}
        {duration ? ` · took ${duration}` : record.finished_at ? "" : " · not finished"}
        {record.allowed_repository && ` · ${record.allowed_repository}`}
      </p>
      {record.summary && <p className="execution-record-summary">{record.summary}</p>}
      {details.length > 0 && (
        <ul className="candidate-bullets execution-record-details">
          {details.map((detail) => (
            <li key={detail}>{detail}</li>
          ))}
        </ul>
      )}
    </li>
  );
}

export default function ExecutionHistory({ proposalId }) {
  const titleId = useId();
  const [history, setHistory] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    const controller = new AbortController();

    fetchExecutionHistory(proposalId, { signal: controller.signal })
      .then((body) => {
        setHistory(body);
        setError(null);
      })
      .catch((err) => {
        if (err.name === "AbortError") return;
        setError(
          err instanceof TypeError
            ? "Could not reach the API. Make sure the FastAPI server and Vite dev server are running."
            : err.message
        );
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });

    return () => controller.abort();
  }, [proposalId, reloadKey]);

  function handleRefresh() {
    setLoading(true);
    setReloadKey((key) => key + 1);
  }

  const records = history?.data ?? [];
  const counts = countByStatus(records);
  const countText = STATUS_ORDER.filter((status) => counts[status] > 0)
    .map((status) => `${executionStatus(status).label} ${counts[status]}`)
    .join(" · ");

  let content;

  if (error) {
    content = (
      <div className="alert alert-error automation-setup-result" role="alert">
        <Icon name="alertCircle" size={18} className="alert-icon" />
        <div className="alert-body">
          <p className="alert-title">Could not load execution history</p>
          <p className="alert-text">{error}</p>
        </div>
      </div>
    );
  } else if (!history) {
    content = <p className="candidate-muted execution-history-note">Loading execution history…</p>;
  } else if (records.length === 0) {
    content = (
      <p className="candidate-muted execution-history-note">
        No executions recorded yet. A run appears here after a matching
        simulated pull request merged event is received while the automation
        is active.
      </p>
    );
  } else {
    content = (
      <>
        <p className="candidate-muted execution-history-note">
          {history.has_more
            ? `Showing the latest ${records.length} executions; older records exist. `
            : `Showing all ${records.length} recorded execution${records.length === 1 ? "" : "s"}. `}
          Shown records: {countText}
        </p>
        <ul className="execution-records">
          {records.map((record) => (
            <ExecutionRecord key={record.source_event_id} record={record} />
          ))}
        </ul>
        <p className="candidate-muted candidate-footnote">
          Only Verified means a matching Slack message was found. Unverified,
          Unknown and In progress runs are not confirmed sends and are never
          retried automatically.
        </p>
      </>
    );
  }

  return (
    <section className="automation-setup execution-history" aria-labelledby={titleId} aria-busy={loading}>
      <div className="execution-history-header">
        <h3 className="candidate-detail-title" id={titleId}>
          Execution history
        </h3>
        <button type="button" className="button button-ghost" onClick={handleRefresh} disabled={loading}>
          {loading ? <span className="spinner" aria-hidden="true" /> : <Icon name="reset" />}
          Refresh history
        </button>
      </div>
      <div aria-live="polite">{content}</div>
    </section>
  );
}
