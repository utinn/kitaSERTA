import { useRef, useState } from "react";
import FormField from "./FormField.jsx";
import Icon from "./Icon.jsx";
import { runAnalysis } from "../api/analysis.js";

// Ranges mirror AnalysisSettings in backend/analysis_api.py.
const FIELDS = {
  min_occurrences: { label: "Min occurrences", min: 1, max: 100 },
  window_days: { label: "Window days", min: 1, max: 90 },
};

const DEFAULTS = { min_occurrences: "3", window_days: "7" };

function validate(values) {
  const errors = {};
  for (const [key, { label, min, max }] of Object.entries(FIELDS)) {
    const number = Number(values[key]);
    if (values[key] === "" || !Number.isInteger(number) || number < min || number > max) {
      errors[key] = `${label} must be a whole number from ${min} to ${max}.`;
    }
  }
  return errors;
}

function describeResult(data) {
  const saved = data.persistence?.saved?.length ?? 0;
  const existing = data.persistence?.already_exists?.length ?? 0;

  if (data.status === "no_candidates" || data.candidates.length === 0) {
    return {
      type: "empty",
      title: "No automation candidates found",
      text: "No repeated pattern met these settings. Try a lower minimum or a longer window.",
    };
  }

  const parts = [];
  if (saved) parts.push(`${saved} new ${saved === 1 ? "candidate" : "candidates"} added for review`);
  if (existing) {
    parts.push(
      `${existing} already existed and ${existing === 1 ? "was" : "were"} left unchanged`
    );
  }

  return {
    type: "success",
    title: "Analysis complete",
    text: `${parts.join("; ")}. No automation was executed.`,
  };
}

/**
 * Runs POST /api/analysis/run with user-chosen detection settings.
 * Calls `onAnalyzed` after a successful run so the parent can refresh.
 */
export default function DetectionSettings({ onAnalyzed }) {
  const [values, setValues] = useState(DEFAULTS);
  const [errors, setErrors] = useState({});
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);
  const loadingRef = useRef(false);

  function handleChange(key) {
    return (event) => {
      setValues((current) => ({ ...current, [key]: event.target.value }));
      if (errors[key]) setErrors((current) => ({ ...current, [key]: undefined }));
    };
  }

  async function handleSubmit(event) {
    event.preventDefault();
    if (loadingRef.current) return;

    const nextErrors = validate(values);
    setErrors(nextErrors);
    if (Object.keys(nextErrors).length > 0) return;

    loadingRef.current = true;
    setLoading(true);
    setResult(null);

    try {
      const data = await runAnalysis({
        min_occurrences: Number(values.min_occurrences),
        window_days: Number(values.window_days),
      });
      setResult(describeResult(data));
      onAnalyzed?.(data);
    } catch (error) {
      setResult({ type: "error", title: "Analysis failed", text: error.message });
    } finally {
      loadingRef.current = false;
      setLoading(false);
    }
  }

  return (
    <section className="card detection-settings" aria-labelledby="detection-title">
      <div className="card-header">
        <div>
          <h2 className="card-title" id="detection-title">
            Detection settings
          </h2>
          <p className="card-description">
            Activity events are grouped by tool and action. A pattern becomes a
            candidate when it repeats at least the minimum number of times
            within the window.
          </p>
        </div>
      </div>

      <form className="form" onSubmit={handleSubmit} noValidate aria-busy={loading}>
        <fieldset className="form-fields detection-fields" disabled={loading}>
          {Object.entries(FIELDS).map(([key, { label, min, max }]) => (
            <FormField
              key={key}
              id={`detection-${key}`}
              label={label}
              hint={`Between ${min} and ${max}.`}
              error={errors[key]}
            >
              {(aria) => (
                <input
                  id={`detection-${key}`}
                  className="input"
                  type="number"
                  inputMode="numeric"
                  min={min}
                  max={max}
                  step={1}
                  value={values[key]}
                  onChange={handleChange(key)}
                  required
                  {...aria}
                />
              )}
            </FormField>
          ))}
        </fieldset>

        <div className="form-actions">
          <button type="submit" className="button button-primary" disabled={loading}>
            {loading ? (
              <span className="spinner" aria-hidden="true" />
            ) : (
              <Icon name="zap" size={14} />
            )}
            {loading ? "Analyzing…" : "Analyze"}
          </button>
        </div>

        {result && (
          <div
            className={`alert ${result.type === "error" ? "alert-error" : result.type === "success" ? "alert-success" : ""} detection-result`}
            role={result.type === "error" ? "alert" : "status"}
          >
            <Icon
              name={result.type === "error" ? "alertCircle" : result.type === "success" ? "checkCircle" : "list"}
              size={18}
              className="alert-icon"
            />
            <div className="alert-body">
              <p className="alert-title">{result.title}</p>
              <p className="alert-text">{result.text}</p>
            </div>
          </div>
        )}
      </form>
    </section>
  );
}
