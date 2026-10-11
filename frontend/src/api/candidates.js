export const CANDIDATES_ENDPOINT = "/api/candidates";

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

const CONFIG_ERRORS = {
  401: "Not authorized to change automation setup. Check the review token configuration and open the app at http://127.0.0.1:5173.",
  404: "This candidate could not be found. It may have been removed.",
  503: "Automation setup storage is not ready. Apply the automation_configs database migration.",
};

function configUrl(proposalId) {
  return `${CANDIDATES_ENDPOINT}/${encodeURIComponent(proposalId)}/automation-config`;
}

async function readConfigResponse(response) {
  const body = await response.json().catch(() => null);

  if (!response.ok) {
    const detail = typeof body?.detail === "string" ? body.detail : null;
    const message =
      CONFIG_ERRORS[response.status] ??
      (response.status === 422 && Array.isArray(body?.detail)
        ? "The setup details were rejected. Check the repository and Slack Channel ID."
        : detail ?? `Request failed (HTTP ${response.status}).`);

    throw Object.assign(new Error(message), { status: response.status });
  }

  return body ?? {};
}

export async function fetchAutomationConfig(proposalId, { signal } = {}) {
  const response = await fetch(configUrl(proposalId), { signal });
  const body = await readConfigResponse(response);
  return body.data ?? null;
}

export async function saveAutomationConfig(proposalId, { allowedRepository, slackChannelId }) {
  let response;

  try {
    response = await fetch(configUrl(proposalId), {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        template_type: "github_pr_merged_to_slack",
        allowed_repository: allowedRepository,
        slack_channel_id: slackChannelId,
      }),
    });
  } catch {
    throw Object.assign(
      new Error("Could not reach the API. Make sure the FastAPI server and Vite dev server are running."),
      { status: 0 }
    );
  }

  const body = await readConfigResponse(response);
  return body.data;
}

export function verifyAutomationConnections(proposalId) {
  return postConfigAction(proposalId, "verify");
}

async function postConfigAction(proposalId, action) {
  let response;

  try {
    response = await fetch(`${configUrl(proposalId)}/${action}`, { method: "POST" });
  } catch {
    throw Object.assign(
      new Error("Could not reach the API. Make sure the FastAPI server and Vite dev server are running."),
      { status: 0 }
    );
  }

  return readConfigResponse(response);
}

export function activateAutomation(proposalId) {
  return postConfigAction(proposalId, "activate");
}

export function disableAutomation(proposalId) {
  return postConfigAction(proposalId, "disable");
}

export async function fetchExecutionHistory(proposalId, { signal } = {}) {
  const response = await fetch(
    `${CANDIDATES_ENDPOINT}/${encodeURIComponent(proposalId)}/executions`,
    { signal }
  );
  const body = await response.json().catch(() => null);

  if (!response.ok) {
    const detail = typeof body?.detail === "string" ? body.detail : null;
    const message =
      response.status === 401
        ? "Not authorized to read execution history. Check the ingest token configuration."
        : detail ?? `Request failed (HTTP ${response.status}).`;
    throw Object.assign(new Error(message), { status: response.status });
  }

  if (!Array.isArray(body?.data)) {
    throw new Error("Invalid API response format");
  }

  return body;
}
