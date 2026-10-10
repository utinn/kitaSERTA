export const API_BASE_URL = "http://127.0.0.1:8000";
export const ACTIVITIES_ENDPOINT = `${API_BASE_URL}/api/activities`;

/**
 * POST an activity to the FastAPI backend, which forwards it to Langflow.
 * Resolves with the response body ({ status, event_id, message }).
 * Rejects with an Error whose message is the API's `detail` when available.
 */
export async function submitActivity(activity) {
  const response = await fetch(ACTIVITIES_ENDPOINT, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(activity),
  });

  const data = await response.json();

  if (!response.ok) {
    const detail =
      typeof data.detail === "string" ? data.detail : `HTTP ${response.status}`;

    throw new Error(detail);
  }

  return data;
}
