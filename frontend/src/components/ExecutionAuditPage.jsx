import { useEffect, useState } from "react";
import Icon from "./Icon.jsx";
import AutomationSetup from "./AutomationSetup.jsx";
import ExecutionHistory from "./ExecutionHistory.jsx";
import { DetectionBadges, StatusPill } from "./CandidateCard.jsx";
import { fetchCandidates } from "../api/candidates.js";
import { asArray, humanize, normalizeStep } from "../utils/candidateFormat.js";
import { isSupportedAutomation } from "../utils/automationSetup.js";
import "./AutomationCandidate.css";

const SETUP_REQUIRED = "Setup required";
const SOP_BLOCKED = "Blocked by SOP";
const UNSUPPORTED = "Not supported yet";
const OPERATIONAL_LABELS = {
  active: { label: "Active", tone: " status-success" },
  disabled: { label: "Disabled", tone: "" },
};

const ONE_TIME_PATTERN = /one[-_ ]?time|once|single[-_ ]?run|manual/i;

function isOneTime(proposal) {
  const mode =
    proposal.execution_mode ?? proposal.run_type ?? proposal.frequency ?? proposal.schedule;
  if (typeof mode === "string" && ONE_TIME_PATTERN.test(mode)) return true;

  const trigger = typeof proposal.trigger === "string" ? proposal.trigger.trim() : "";
  return !trigger || ["none", "null"].includes(trigger.toLowerCase()) || ONE_TIME_PATTERN.test(trigger);
}

function formatDate(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString();
}

function StateCard({ icon, title, text, children, role }) {
  return (
    <section className="card empty-state" role={role} aria-live="polite">
      <span className="empty-state-icon" aria-hidden="true">
        <Icon name={icon} size={18} />
      </span>
      <h2 className="card-title">{title}</h2>
      <p className="card-description">{text}</p>
      {children}
    </section>
  );
}

function AuditCard({ candidate }) {
  const proposal =
    candidate.proposal && typeof candidate.proposal === "object" ? candidate.proposal : {};
  const title = candidate.title ?? proposal.title ?? "Untitled proposal";
  const validationResult = candidate.validation_result ?? proposal.validation_result;
  const sopStatus = candidate.sop_status ?? proposal.sop_status;
  const steps = asArray(proposal.steps).map(normalizeStep).filter((step) => step.action);
  const blockedReasons = asArray(proposal.blocked_reasons);
  const isBlocked = validationResult === "blocked";
  const oneTime = isOneTime(proposal);
  const lifecycleAction = oneTime ? "Archive" : "Disable";
  const supported = isSupportedAutomation(proposal, title);
  const canSetUp = supported && !isBlocked;
  const [operationalStatus, setOperationalStatus] = useState(null);
  const confirmed = canSetUp ? OPERATIONAL_LABELS[operationalStatus] : undefined;
  const executionStatus = confirmed
    ? confirmed.label
    : !supported
      ? UNSUPPORTED
      : isBlocked
        ? SOP_BLOCKED
        : SETUP_REQUIRED;

  return (
    <article className="card candidate-card">
      <div className="card-header">
        <div className="candidate-heading">
          <h2 className="card-title">{title}</h2>
          {proposal.problem_summary && (
            <p className="card-description">{proposal.problem_summary}</p>
          )}
        </div>
        <div className="candidate-header-badges">
          <DetectionBadges settings={proposal.detection_settings} />
        </div>
      </div>

      <dl className="candidate-meta">
        <div>
          <dt>Review status</dt>
          <dd>
            <StatusPill value={candidate.review_status} />
          </dd>
        </div>
        <div>
          <dt>Execution status</dt>
          <dd>
            <span className={`status-pill${confirmed?.tone ?? ""}`}>{executionStatus}</span>
          </dd>
        </div>
        <div>
          <dt>SOP validation</dt>
          <dd>
            <StatusPill value={validationResult} />
            {!validationResult && "—"}
          </dd>
        </div>
        <div>
          <dt>Reviewed</dt>
          <dd className="audit-date">{formatDate(candidate.updated_at)}</dd>
        </div>
      </dl>

      {(isBlocked || blockedReasons.length > 0 || sopStatus === "missing") && (
        <div className="candidate-blocked" role="note">
          <Icon name="alertCircle" size={16} className="alert-icon" />
          <div>
            <p className="candidate-blocked-title">Original warnings</p>
            <ul className="candidate-bullets">
              {isBlocked && <li>Blocked by SOP validation.</li>}
              {sopStatus && sopStatus !== "present" && (
                <li>SOP status: {humanize(sopStatus)}.</li>
              )}
              {blockedReasons.map((reason) => (
                <li key={reason}>{reason}</li>
              ))}
            </ul>
          </div>
        </div>
      )}

      <div className="candidate-details">
        <div className="candidate-detail">
          <h3 className="candidate-detail-title">Workflow steps</h3>
          {!proposal.trigger && steps.length === 0 ? (
            <p className="candidate-muted">No workflow proposed.</p>
          ) : (
            <ol className="workflow-steps">
              {proposal.trigger && (
                <li className="workflow-step">
                  <span className="tag">Trigger</span>
                  <code className="mono">{proposal.trigger}</code>
                </li>
              )}
              {steps.map((step, index) => (
                <li className="workflow-step" key={`${index}-${step.action}`}>
                  <span className="tag">Step {index + 1}</span>
                  <span>
                    {step.action}
                    {step.tool && <span className="candidate-muted"> · {step.tool}</span>}
                  </span>
                </li>
              ))}
            </ol>
          )}
        </div>
      </div>

      {canSetUp ? (
        <AutomationSetup
          proposalId={candidate.proposal_id}
          onStatusChange={setOperationalStatus}
        />
      ) : (
        <p className="candidate-review-note" role="note">
          {supported
            ? "This proposal is blocked by SOP validation and cannot be set up."
            : "Only GitHub pull request merged → Slack notification automations can be set up in this version."}
        </p>
      )}

      {candidate.proposal_id && <ExecutionHistory proposalId={candidate.proposal_id} />}

      <div className="candidate-footer">
        <span className="candidate-muted">
          {operationalStatus === "active" && canSetUp
            ? "Approved by human review. Active for simulated events; see Execution history for runs."
            : "Approved by human review. Not active."}
        </span>
        {!canSetUp && (
          <button
            type="button"
            className="button button-ghost"
            disabled
            title={`${lifecycleAction} becomes available after execution`}
          >
            {lifecycleAction}
            <span className="visually-hidden">
              {" "}
              ({oneTime ? "one-time automation" : "recurring automation"}, available after execution)
            </span>
          </button>
        )}
      </div>
    </article>
  );
}

export default function ExecutionAuditPage() {
  const [candidates, setCandidates] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    const controller = new AbortController();

    fetchCandidates({ limit: 50, reviewStatus: "approved", signal: controller.signal })
      .then(setCandidates)
      .catch((err) => {
        if (err.name === "AbortError") return;
        setCandidates([]);
        setError(
          err instanceof TypeError
            ? "Could not reach the candidates API. Make sure the FastAPI server and Vite dev server are running."
            : err.message
        );
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });

    return () => controller.abort();
  }, [reloadKey]);

  function handleRetry() {
    setLoading(true);
    setError(null);
    setReloadKey((key) => key + 1);
  }

  let body;

  if (loading) {
    body = (
      <StateCard
        icon="reset"
        title="Loading approved automations…"
        text="Fetching proposals that passed human review."
      />
    );
  } else if (error) {
    body = (
      <StateCard icon="alertCircle" title="Could not load the execution audit" text={error} role="alert">
        <button type="button" className="button button-ghost empty-state-action" onClick={handleRetry}>
          <Icon name="reset" />
          Try again
        </button>
      </StateCard>
    );
  } else if (candidates.length === 0) {
    body = (
      <StateCard
        icon="fileText"
        title="No approved automations yet"
        text="Proposals accepted on the Automation Candidate page will appear here."
      />
    );
  } else {
    body = (
      <ul className="candidate-list">
        {candidates.map((candidate, index) => (
          <li key={candidate.proposal_id ?? index}>
            <AuditCard candidate={candidate} />
          </li>
        ))}
      </ul>
    );
  }

  return (
    <>
      <div className="page-header">
        <p className="page-eyebrow">Automation</p>
        <h1 className="page-title">Execution audit</h1>
        <p className="page-description">
          Approved automation proposals with their workflow, original
          validation warnings, and review and execution status.
        </p>
      </div>

      <div aria-busy={loading}>{body}</div>
    </>
  );
}
