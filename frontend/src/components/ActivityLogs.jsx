
import { useEffect, useState } from "react";
import "./ActivityLogs.css";

const API_URL = "http://127.0.0.1:8000/api/activities";

function formatDate(value) {
  if (!value) return "—";

  const date = new Date(value);

  if (Number.isNaN(date.getTime())) {
    return "—";
  }

  return date.toLocaleString("en-US", {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

export default function ActivityLogs() {
  const [activities, setActivities] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    const controller = new AbortController();

    async function fetchActivities() {
      try {
        setLoading(true);
        setError(null);

        const response = await fetch(
          `${API_URL}?limit=20`,
          { signal: controller.signal }
        );

        if (!response.ok) {
          throw new Error(
            `Failed to fetch activities: ${response.status}`
          );
        }

        const result = await response.json();

        if (!Array.isArray(result.data)) {
          throw new Error("Invalid API response format");
        }

        setActivities(result.data);

      } catch (err) {
        if (err.name !== "AbortError") {
          setError(err.message);
        }
      } finally {
        if (!controller.signal.aborted) {
          setLoading(false);
        }
      }
    }

    fetchActivities();

    return () => controller.abort();
  }, []);

  return (
    <section className="activity-logs">
      <div className="activity-logs__header">
        <div>
          <h2>Activity Logs</h2>
          <p>
            Recent digital activities recorded by the system.
          </p>
        </div>
      </div>

      {loading && (
        <p className="activity-logs__message">
          Loading activities...
        </p>
      )}

      {error && (
        <p className="activity-logs__error" role="alert">
          Error: {error}
        </p>
      )}

      {!loading && !error && activities.length === 0 && (
        <p className="activity-logs__message">
          No activities recorded yet.
        </p>
      )}

      {!loading && !error && activities.length > 0 && (
        <div className="activity-logs__table-wrapper">
          <table className="activity-logs__table">
            <thead>
              <tr>
                <th>Timestamp</th>
                <th>Employee</th>
                <th>Tool</th>
                <th>Action</th>
                <th>Project</th>
              </tr>
            </thead>

            <tbody>
              {activities.map((activity) => (
                <tr key={activity.id}>
                  <td>{formatDate(activity.timestamp)}</td>
                  <td>{activity.employee_id ?? "—"}</td>
                  <td>{activity.tool ?? "—"}</td>
                  <td>{activity.action ?? "—"}</td>
                  <td>{activity.project ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
