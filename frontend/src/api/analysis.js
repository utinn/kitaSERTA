export const ANALYSIS_ENDPOINT = "/api/analysis/run";

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
