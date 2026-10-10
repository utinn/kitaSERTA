import { useRef, useState } from "react";
import FormField from "./FormField.jsx";
import Icon from "./Icon.jsx";
import PayloadPreview from "./PayloadPreview.jsx";
import PipelineCard from "./PipelineCard.jsx";
import SubmissionResult from "./SubmissionResult.jsx";
import { API_BASE_URL, submitActivity } from "../api/activities.js";

const ACTIVITY_TYPE = "pr_merged";

const FIELD_LABELS = {
  employee_id: "Employee ID",
  title: "Activity title",
  repository: "Repository",
};

export default function ActivitySubmissionPage() {
  const [employeeId, setEmployeeId] = useState("");
  const [title, setTitle] = useState("");
  const [repository, setRepository] = useState("");
  const [preview, setPreview] = useState(null);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);
  const [errors, setErrors] = useState({});

  const inputRefs = {
    employee_id: useRef(null),
    title: useRef(null),
    repository: useRef(null),
  };

  const activity = {
    employee_id: employeeId.trim(),
    activity_type: ACTIVITY_TYPE,
    title: title.trim(),
    repository: repository.trim(),
  };

  function handleFieldChange(key, setter) {
    return (event) => {
      setter(event.target.value);
      setPreview(null);
      if (errors[key]) {
        setErrors((current) => ({ ...current, [key]: undefined }));
      }
    };
  }

  async function handleSubmit(event) {
    event.preventDefault();

    const missing = Object.keys(FIELD_LABELS).filter((key) => !activity[key]);

    if (missing.length > 0) {
      setErrors(
        Object.fromEntries(
          missing.map((key) => [key, `${FIELD_LABELS[key]} is required.`])
        )
      );
      inputRefs[missing[0]].current?.focus();
      return;
    }

    setErrors({});
    setPreview(activity);
    setLoading(true);
    setResult(null);

    try {
      const data = await submitActivity(activity);

      setResult({
        type: "success",
        message: data.message,
        eventId: data.event_id,
      });
    } catch (error) {
      setResult({
        type: "error",
        message:
          error instanceof TypeError
            ? `Could not reach the API at ${API_BASE_URL}. Make sure the FastAPI server is running.`
            : error.message,
      });
    } finally {
      setLoading(false);
    }
  }

  function handleReset() {
    setEmployeeId("");
    setTitle("");
    setRepository("");
    setPreview(null);
    setResult(null);
    setErrors({});
    inputRefs.employee_id.current?.focus();
  }

  const isPristine = !employeeId && !title && !repository && !result;

  return (
    <>
      <div className="page-header">
        <p className="page-eyebrow">Creator Dashboard</p>
        <h1 className="page-title">Submit activity</h1>
        <p className="page-description">
          Record a digital activity so kitaSERTA can analyze it for
          automation opportunities.
        </p>
      </div>

      <div className="page-grid">
        <section className="card" aria-labelledby="form-title">
          <div className="card-header">
            <div>
              <h2 className="card-title" id="form-title">
                Activity details
              </h2>
              <p className="card-description">All fields are required.</p>
            </div>
          </div>

          <form
            className="form"
            onSubmit={handleSubmit}
            noValidate
            aria-busy={loading}
          >
            <fieldset className="form-fields" disabled={loading}>
              <FormField
                id="employee"
                label="Employee ID"
                hint="Identifier of the person who performed the activity."
                error={errors.employee_id}
              >
                {(aria) => (
                  <input
                    ref={inputRefs.employee_id}
                    id="employee"
                    className="input"
                    value={employeeId}
                    onChange={handleFieldChange("employee_id", setEmployeeId)}
                    placeholder="dev-001"
                    autoComplete="off"
                    spellCheck={false}
                    required
                    {...aria}
                  />
                )}
              </FormField>

              <FormField
                id="activity"
                label="Activity type"
                hint="Only GitHub pull request merges are supported for now."
              >
                {(aria) => (
                  <div className="select-wrapper">
                    <select
                      id="activity"
                      className="input select"
                      value={ACTIVITY_TYPE}
                      disabled
                      {...aria}
                    >
                      <option value={ACTIVITY_TYPE}>
                        GitHub — Pull Request Merged
                      </option>
                    </select>
                  </div>
                )}
              </FormField>

              <FormField
                id="title"
                label="Activity title"
                error={errors.title}
              >
                {(aria) => (
                  <input
                    ref={inputRefs.title}
                    id="title"
                    className="input"
                    value={title}
                    onChange={handleFieldChange("title", setTitle)}
                    placeholder="Merge authentication feature"
                    autoComplete="off"
                    required
                    {...aria}
                  />
                )}
              </FormField>

              <FormField
                id="repository"
                label="Repository"
                hint="Use the owner/name format."
                error={errors.repository}
              >
                {(aria) => (
                  <input
                    ref={inputRefs.repository}
                    id="repository"
                    className="input mono"
                    value={repository}
                    onChange={handleFieldChange("repository", setRepository)}
                    placeholder="team/kitaserta"
                    autoComplete="off"
                    spellCheck={false}
                    required
                    {...aria}
                  />
                )}
              </FormField>
            </fieldset>

            <div className="form-actions">
              <button
                type="button"
                className="button button-ghost"
                onClick={handleReset}
                disabled={loading || isPristine}
              >
                <Icon name="reset" size={14} />
                Clear
              </button>
              <button
                type="submit"
                className="button button-primary"
                disabled={loading}
              >
                {loading && <span className="spinner" aria-hidden="true" />}
                {loading ? "Submitting…" : "Submit activity"}
              </button>
            </div>
          </form>

          {result && (
            <div className="card-section">
              <SubmissionResult result={result} />
            </div>
          )}
        </section>

        <div className="page-aside">
          <PayloadPreview payload={preview ?? activity} submitted={!!preview} />
          <PipelineCard />
        </div>
      </div>
    </>
  );
}
