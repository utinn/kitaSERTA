// Relative on purpose: the Vite dev proxy forwards this to FastAPI.
export const ANALYSIS_ENDPOINT = "/api/analysis/run";

/**
 * POST detection settings to run workflow analysis (Flow 2).
 * Resolves with { status, settings, candidates, persistence }.
 * Rejects with an Error whose message is the API's `detail` when available.
 * Running analysis only proposes candidates; it never executes automations.
 */
export async function runAnalysis({ min_occurrences, window_days }) {
  let response;

  try {
    response = await fetch(ANALYSIS_ENDPOINT, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ min_occurrences, window_days }),
    });
  } catch {
    throw new Error(
      "Could not reach the analysis API. Make sure the FastAPI server and Vite dev server are running."
    );
  }

  const body = await response.json().catch(() => null);

  if (!response.ok) {
    const detail = typeof body?.detail === "string" ? body.detail : null;
    throw new Error(
      response.status === 422
        ? "Invalid detection settings. Check the allowed ranges."
        : detail ?? `Analysis failed (HTTP ${response.status}).`
    );
  }

  if (!body || !Array.isArray(body.candidates)) {
    throw new Error("Invalid analysis response format");
  }

  return body;
}
