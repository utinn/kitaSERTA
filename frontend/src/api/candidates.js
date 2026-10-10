// Relative on purpose: the Vite dev proxy forwards this to FastAPI and adds
// the server-side auth header. Never add credentials here.
export const CANDIDATES_ENDPOINT = "/api/candidates";

/**
 * GET automation candidates. Resolves with the `data` array of the response.
 * `reviewStatus` optionally filters by "pending_review" | "approved" | "rejected".
 * Rejects with an Error whose message is the API's `detail` when available.
 */
export async function fetchCandidates({ limit = 20, reviewStatus, signal } = {}) {
  const params = new URLSearchParams({ limit: String(limit) });
  if (reviewStatus) params.set("review_status", reviewStatus);

  const response = await fetch(`${CANDIDATES_ENDPOINT}?${params}`, {
    signal,
  });

  const body = await response.json().catch(() => null);

  if (!response.ok) {
    const detail =
      typeof body?.detail === "string" ? body.detail : `HTTP ${response.status}`;

    throw new Error(detail);
  }

  if (!Array.isArray(body?.data)) {
    throw new Error("Invalid API response format");
  }

  return body.data;
}

const DECISION_ERRORS = {
  401: "Not authorized to submit review decisions. Check the review token configuration and open the app at http://127.0.0.1:5173.",
  403: "You do not have permission to review this candidate.",
  404: "This candidate could not be found. It may have been removed.",
  409: "This candidate was already reviewed or is no longer awaiting a decision.",
};

/**
 * PATCH a human review decision ("approved" | "rejected") for one candidate.
 * Saving a decision never executes an automation.
 * Rejects with an Error carrying `status` (HTTP code, or 0 for network errors).
 */
export async function submitDecision(proposalId, decision) {
  let response;

  try {
    response = await fetch(
      `${CANDIDATES_ENDPOINT}/${encodeURIComponent(proposalId)}/decision`,
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ decision }),
      }
    );
  } catch {
    throw Object.assign(
      new Error("Could not reach the API. Make sure the FastAPI server and Vite dev server are running."),
      { status: 0 }
    );
  }

  const body = await response.json().catch(() => null);

  if (!response.ok) {
    const detail = typeof body?.detail === "string" ? body.detail : null;
    const message =
      DECISION_ERRORS[response.status] ??
      (response.status >= 500
        ? `Server error (${response.status})${detail ? `: ${detail}` : ""}. Please try again.`
        : detail ?? `Request failed (HTTP ${response.status}).`);

    throw Object.assign(new Error(message), { status: response.status });
  }

  return body ?? {};
}
