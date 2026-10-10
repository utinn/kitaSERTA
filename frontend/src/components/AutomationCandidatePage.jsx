import { useCallback, useEffect, useRef, useState } from "react";
import CandidateCard from "./CandidateCard.jsx";
import DetectionSettings from "./DetectionSettings.jsx";
import Icon from "./Icon.jsx";
import { fetchCandidates } from "../api/candidates.js";
import "./AutomationCandidate.css";

// This page is the review queue: decided proposals move to Execution Audit.
const REVIEW_STATUS = "pending_review";

const REVIEW_STEPS = [
  { title: "Candidate", text: "Flow 2 detects a repeated activity pattern." },
  { title: "Review details", text: "Inspect evidence, workflow and SOP checks." },
  { title: "Decision", text: "Accept or reject the proposed automation." },
];

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

export default function AutomationCandidatePage() {
  const [candidates, setCandidates] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [notice, setNotice] = useState(null);
  const refreshController = useRef(null);

  // Abort any in-flight background refresh on unmount.
  useEffect(() => () => refreshController.current?.abort(), []);

  useEffect(() => {
    const controller = new AbortController();

    fetchCandidates({
      limit: 20,
      reviewStatus: REVIEW_STATUS,
      signal: controller.signal,
    })
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

  const handleRetry = useCallback(() => {
    setLoading(true);
    setError(null);
    setReloadKey((key) => key + 1);
  }, []);

  // Re-fetch the pending queue in the background, keeping the current list
  // on failure; a later visit re-fetches it.
  const refreshCandidates = useCallback(() => {
    refreshController.current?.abort();
    const controller = new AbortController();
    refreshController.current = controller;

    fetchCandidates({
      limit: 20,
      reviewStatus: REVIEW_STATUS,
      signal: controller.signal,
    })
      .then((data) => {
        setCandidates(data);
        setError(null);
      })
      .catch(() => {});
  }, []);

  // Called by a card after the decision API confirmed the save. The decided
  // proposal leaves the review queue; approved ones appear in Execution Audit.
  const handleDecisionSaved = useCallback(
    (candidate, result) => {
      const title = candidate.title ?? candidate.proposal_id;
      const approved = result.review_status === "approved";

      setCandidates((current) =>
        current.filter((item) => item.proposal_id !== candidate.proposal_id)
      );

      setNotice(
        approved
          ? `"${title}" was accepted and moved to Execution Audit, awaiting execution. No automation was executed.`
          : `"${title}" was rejected. No automation was executed.`
      );

      refreshCandidates();
    },
    [refreshCandidates]
  );

  let body;

  if (loading) {
    body = (
      <StateCard
        icon="reset"
        title="Loading automation candidates…"
        text="Fetching the latest proposals from workflow analysis."
      />
    );
  } else if (error) {
    body = (
      <StateCard
        icon="alertCircle"
        title="Could not load automation candidates"
        text={error}
        role="alert"
      >
        <button
          type="button"
          className="button button-ghost empty-state-action"
          onClick={handleRetry}
        >
          <Icon name="reset" />
          Try again
        </button>
      </StateCard>
    );
  } else if (candidates.length === 0) {
    body = (
      <StateCard
        icon="zap"
        title="No candidates awaiting review"
        text="Run an analysis above. New pending proposals will appear here."
      />
    );
  } else {
    body = (
      <ul className="candidate-list">
        {candidates.map((candidate, index) => (
          <li key={candidate.proposal_id ?? index}>
            <CandidateCard
              candidate={candidate}
              onDecisionSaved={handleDecisionSaved}
            />
          </li>
        ))}
      </ul>
    );
  }

  return (
    <>
      <div className="page-header">
        <p className="page-eyebrow">Automation</p>
        <h1 className="page-title">Automation candidates</h1>
        <p className="page-description">
          Review automation proposals generated from workflow analysis and
          decide whether each one should move forward.
        </p>
      </div>

      <ol className="review-steps" aria-label="Review process">
        {REVIEW_STEPS.map((step, index) => (
          <li className="review-step" key={step.title}>
            <span className="pipeline-index" aria-hidden="true">
              {index + 1}
            </span>
            <div>
              <p className="pipeline-title">{step.title}</p>
              <p className="pipeline-text">{step.text}</p>
            </div>
          </li>
        ))}
      </ol>

      <DetectionSettings onAnalyzed={refreshCandidates} />

      {notice && (
        <div className="alert alert-success candidate-notice" role="status">
          <Icon name="checkCircle" size={18} className="alert-icon" />
          <div className="alert-body">
            <p className="alert-title">Review decision saved</p>
            <p className="alert-text">{notice}</p>
          </div>
          <button
            type="button"
            className="icon-button icon-button-sm"
            onClick={() => setNotice(null)}
            aria-label="Dismiss message"
          >
            <Icon name="close" size={14} />
          </button>
        </div>
      )}

      <div aria-busy={loading}>{body}</div>
    </>
  );
}
