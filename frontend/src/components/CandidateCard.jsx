import { useEffect, useId, useRef, useState } from "react";
import Icon from "./Icon.jsx";
import { submitDecision } from "../api/candidates.js";
import { asArray, humanize, normalizeStep } from "../utils/candidateFormat.js";

// Tone per database-managed status value; unknown values render neutral.
const TONES = {
  approved: "success",
  accepted: "success",
  rejected: "danger",
  pass: "success",
  blocked: "danger",
  missing: "danger",
};

// Readable names for known trigger slugs; others fall back to humanize().
const TRIGGER_LABELS = {
  GITHUB_PULL_REQUEST_MERGED: "GitHub pull request merges",
};

function formatTrigger(trigger) {
  if (typeof trigger !== "string" || !trigger) return null;
  return TRIGGER_LABELS[trigger] ?? humanize(trigger.toLowerCase());
}

function formatFlag(value) {
  if (typeof value !== "boolean") return "—";
  return value ? "Yes" : "No";
}

const DECISION_COPY = {
  approved: {
    action: "Accept",
    title: "Accept this proposal for follow-up?",
    text: "Accepting records that the proposal should move to follow-up or further review. It does not authorize or execute any automation, and SOP validation results stay unchanged.",
  },
  rejected: {
    action: "Reject",
    title: "Reject this proposal?",
    text: "Rejecting records that the proposal has been declined. No automation will be executed.",
  },
};

export function StatusPill({ value }) {
  const label = humanize(value);
  if (!label) return null;
  const tone = TONES[value];
  return (
    <span className={`status-pill${tone ? ` status-${tone}` : ""}`}>
      {label}
    </span>
  );
}

// Settings snapshot saved with the proposal when it was detected.
// Older candidates have no snapshot and render no badges.
export function DetectionBadges({ settings }) {
  if (!settings || typeof settings !== "object") return null;
  const { min_occurrences: minOccurrences, window_days: windowDays } = settings;
  const groupBy = Array.isArray(settings.group_by) ? settings.group_by.join(" + ") : null;
  const title = groupBy ? `Detected by grouping ${groupBy}` : undefined;

  return (
    <>
      {typeof minOccurrences === "number" && (
        <span className="badge detection-badge" title={title}>
          Min Occurrences: {minOccurrences}
        </span>
      )}
      {typeof windowDays === "number" && (
        <span className="badge detection-badge" title={title}>
          Window Days: {windowDays}
        </span>
      )}
    </>
  );
}

function DetailSection({ title, children }) {
  return (
    <div className="candidate-detail">
      <h3 className="candidate-detail-title">{title}</h3>
      {children}
    </div>
  );
}

/**
 * Renders one record from GET /api/candidates.
 * `candidate.proposal` is the AI-generated proposal; `review_status`,
 * `validation_result` and `sop_status` at the top level are managed by the
 * database and take precedence over copies inside the proposal.
 *
 * Accept/Reject are enabled only while `review_status === "pending_review"`.
 * A decision records human review only; it never executes an automation.
 */
export default function CandidateCard({ candidate, onDecisionSaved }) {
  const [expanded, setExpanded] = useState(false);
  const [confirming, setConfirming] = useState(null);
  const [submitting, setSubmitting] = useState(false);
  const [decisionError, setDecisionError] = useState(null);
  const [retryable, setRetryable] = useState(true);
  const submittingRef = useRef(false);
  const confirmRef = useRef(null);
  const detailsId = useId();
  const confirmTitleId = useId();

  useEffect(() => {
    if (confirming) confirmRef.current?.focus();
  }, [confirming]);

  const proposal =
    candidate.proposal && typeof candidate.proposal === "object"
      ? candidate.proposal
      : {};

  const title = candidate.title ?? proposal.title ?? "Untitled proposal";
  const reviewStatus = candidate.review_status;
  const validationResult =
    candidate.validation_result ?? proposal.validation_result;
  const sopStatus = candidate.sop_status ?? proposal.sop_status;
  const eventIds = asArray(
    candidate.evidence_event_ids ?? proposal.evidence_event_ids
  );
  const steps = asArray(proposal.steps)
    .map(normalizeStep)
    .filter((step) => step.action);
  const blockedReasons = asArray(proposal.blocked_reasons);
  const minutesSaved = proposal.expected_minutes_saved_per_run;
  const triggerLabel = formatTrigger(proposal.trigger);
  const isBlocked = validationResult === "blocked";
  const showBlocked = isBlocked || blockedReasons.length > 0;
  const canDecide =
    reviewStatus === "pending_review" &&
    Boolean(candidate.proposal_id) &&
    !submitting;

  function startDecision(decision) {
    setDecisionError(null);
    setRetryable(true);
    setConfirming(decision);
  }

  function cancelDecision() {
    setConfirming(null);
    setDecisionError(null);
  }

  async function confirmDecision() {
    // Guard against double submission before React re-renders.
    if (submittingRef.current || !confirming) return;
    submittingRef.current = true;
    setSubmitting(true);
    setDecisionError(null);

    try {
      const result = await submitDecision(candidate.proposal_id, confirming);
      setConfirming(null);
      onDecisionSaved?.(candidate, result);
    } catch (error) {
      setDecisionError(error.message);
      // Not found / already reviewed: retrying cannot succeed.
      setRetryable(![404, 409].includes(error.status));
    } finally {
      submittingRef.current = false;
      setSubmitting(false);
    }
  }

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
          <span className="candidate-review">
            <span className="visually-hidden">Review status: </span>
            <StatusPill value={reviewStatus ?? "unknown"} />
          </span>
        </div>
      </div>

      <dl className="candidate-meta">
        <div>
          <dt>SOP validation</dt>
          <dd>
            <StatusPill value={validationResult} />
            {!validationResult && "—"}
          </dd>
        </div>
        <div>
          <dt>SOP status</dt>
          <dd>
            <StatusPill value={sopStatus} />
            {!sopStatus && "—"}
          </dd>
        </div>
        <div>
          <dt>Risk level</dt>
          <dd>{humanize(proposal.risk_level) ?? "—"}</dd>
        </div>
        <div>
          <dt>Confidence</dt>
          <dd>{humanize(proposal.confidence) ?? "—"}</dd>
        </div>
      </dl>

      {showBlocked && (
        <div className="candidate-blocked" role="note">
          <Icon name="alertCircle" size={16} className="alert-icon" />
          <div>
            <p className="candidate-blocked-title">
              {isBlocked ? "Blocked by SOP validation" : "Validation notes"}
            </p>
            {blockedReasons.length > 0 ? (
              <ul className="candidate-bullets">
                {blockedReasons.map((reason) => (
                  <li key={reason}>{reason}</li>
                ))}
              </ul>
            ) : (
              <p className="candidate-muted">No reason was provided.</p>
            )}
          </div>
        </div>
      )}

      {expanded && (
        <div className="candidate-details" id={detailsId}>
          <DetailSection title="Proposed workflow">
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
                      {step.tool && (
                        <span className="candidate-muted"> · {step.tool}</span>
                      )}
                    </span>
                  </li>
                ))}
              </ol>
            )}
          </DetailSection>

          <DetailSection title="Detection evidence">
            {eventIds.length === 0 ? (
              <p className="candidate-muted">No evidence events linked.</p>
            ) : (
              <>
                <p className="evidence-summary">
                  <strong>{eventIds.length}</strong> supporting{" "}
                  {eventIds.length === 1 ? "event" : "events"}
                  {triggerLabel && (
                    <span className="candidate-muted"> · {triggerLabel}</span>
                  )}
                </p>
                <p className="candidate-muted">
                  Events linked to this proposal by workflow analysis. The
                  count alone does not confirm repeated manual work.
                </p>
                <details className="evidence-ids">
                  <summary>View event IDs</summary>
                  <div className="candidate-tags">
                    {eventIds.map((id) => (
                      <code className="tag mono" key={id}>
                        {id}
                      </code>
                    ))}
                  </div>
                </details>
              </>
            )}
          </DetailSection>

          <DetailSection title="Approval & execution">
            <ul className="validation-list">
              <li>
                Human approval required:{" "}
                {formatFlag(proposal.human_approval_required)}
              </li>
              <li>
                Auto-execution allowed:{" "}
                {formatFlag(proposal.auto_execute_allowed)}
              </li>
              {typeof minutesSaved === "number" && (
                <li>Estimated time saved: ~{minutesSaved} min per run</li>
              )}
            </ul>
            <p className="candidate-muted candidate-footnote">
              Risk level, confidence and time savings are AI-generated
              estimates. They are not verified metrics and do not authorize
              execution.
            </p>
          </DetailSection>
        </div>
      )}

      {reviewStatus === "approved" && isBlocked && (
        <p className="candidate-review-note" role="note">
          Accepted for follow-up only. This proposal is still blocked by SOP
          validation and is not authorized for execution.
        </p>
      )}

      {confirming && (
        <div
          className="candidate-confirm"
          role="alertdialog"
          aria-labelledby={confirmTitleId}
          tabIndex={-1}
          ref={confirmRef}
          onKeyDown={(event) => {
            if (event.key === "Escape" && !submitting) cancelDecision();
          }}
        >
          <p className="candidate-detail-title" id={confirmTitleId}>
            {DECISION_COPY[confirming].title}
          </p>
          <p className="candidate-muted">{DECISION_COPY[confirming].text}</p>

          {decisionError && (
            <div className="alert alert-error candidate-confirm-error" role="alert">
              <Icon name="alertCircle" size={16} className="alert-icon" />
              <div className="alert-body">
                <p className="alert-title">Decision not saved</p>
                <p className="alert-text">{decisionError}</p>
              </div>
            </div>
          )}

          <div className="candidate-confirm-actions">
            <button
              type="button"
              className="button button-ghost"
              disabled={submitting}
              onClick={cancelDecision}
            >
              Cancel
            </button>
            {retryable && (
              <button
                type="button"
                className="button button-primary button-compact"
                disabled={submitting}
                aria-busy={submitting}
                onClick={confirmDecision}
              >
                {submitting && <span className="spinner" aria-hidden="true" />}
                {submitting
                  ? "Saving…"
                  : decisionError
                    ? "Try again"
                    : `Confirm ${DECISION_COPY[confirming].action.toLowerCase()}`}
              </button>
            )}
          </div>
        </div>
      )}

      <div className="candidate-footer">
        <button
          type="button"
          className="button button-ghost"
          aria-expanded={expanded}
          aria-controls={expanded ? detailsId : undefined}
          onClick={() => setExpanded((value) => !value)}
        >
          <Icon name="chevronDown" className={expanded ? "is-flipped" : undefined} />
          {expanded ? "Hide details" : "Review details"}
        </button>

        <div className="candidate-actions">
          {reviewStatus !== "pending_review" && (
            <span className="candidate-muted">Review decision recorded</span>
          )}
          <button
            type="button"
            className="button button-ghost"
            disabled={!canDecide || confirming !== null}
            onClick={() => startDecision("rejected")}
          >
            Reject
          </button>
          <button
            type="button"
            className="button button-primary button-compact"
            disabled={!canDecide || confirming !== null}
            onClick={() => startDecision("approved")}
          >
            Accept
          </button>
        </div>
      </div>
    </article>
  );
}
