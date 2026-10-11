import { useEffect, useId, useRef, useState } from "react";
import FormField from "./FormField.jsx";
import Icon from "./Icon.jsx";
import {
  activateAutomation,
  disableAutomation,
  fetchAutomationConfig,
  saveAutomationConfig,
  verifyAutomationConnections,
} from "../api/candidates.js";
import { validateSetup } from "../utils/automationSetup.js";

const EMPTY = { repository: "", channelId: "" };

const CHECK_LABELS = {
  candidate: "Candidate",
  configuration: "Saved setup",
  slack_account: "Slack account",
  slack_channel: "Slack channel",
  execution_flow: "Execution Flow",
};

const OPERATIONAL_STATUS = {
  setup_required: { label: "Setup required", tone: "" },
  active: { label: "Active", tone: " status-success" },
  disabled: { label: "Disabled", tone: "" },
};
const CHECK_STATUS = {
  verified: { label: "Verified", tone: " status-success" },
  failed: { label: "Failed", tone: " status-danger" },
  unverified: { label: "Unverified", tone: "" },
  blocked: { label: "Not checked", tone: "" },
};

function CheckPill({ status }) {
  const { label, tone } = CHECK_STATUS[status] ?? CHECK_STATUS.unverified;
  return <span className={`status-pill${tone}`}>{label}</span>;
}

function toValues(config) {
  return config
    ? { repository: config.allowed_repository ?? "", channelId: config.slack_channel_id ?? "" }
    : EMPTY;
}

function formatDate(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date.toLocaleString();
}

export default function AutomationSetup({ proposalId, onStatusChange }) {
  const [values, setValues] = useState(EMPTY);
  const [errors, setErrors] = useState({});
  const [saved, setSaved] = useState(null);
  const [loading, setLoading] = useState(Boolean(proposalId));
  const [loadError, setLoadError] = useState(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [saving, setSaving] = useState(false);
  const [result, setResult] = useState(null);
  const [verification, setVerification] = useState(null);
  const [verifying, setVerifying] = useState(false);
  const [verifyError, setVerifyError] = useState(null);
  const [acting, setActing] = useState(null);
  const [actionResult, setActionResult] = useState(null);
  const savingRef = useRef(false);
  const status = saved?.operational_status ?? null;

  useEffect(() => {
    onStatusChange?.(status);
  }, [status, onStatusChange]);
  const titleId = useId();
  const idPrefix = `setup-${proposalId ?? titleId}`;

  useEffect(() => {
    if (!proposalId) return undefined;
    const controller = new AbortController();

    fetchAutomationConfig(proposalId, { signal: controller.signal })
      .then((config) => {
        setSaved(config);
        setValues(toValues(config));
      })
      .catch((error) => {
        if (error.name === "AbortError") return;
        setLoadError(
          error instanceof TypeError
            ? "Could not reach the API. Make sure the FastAPI server and Vite dev server are running."
            : error.message
        );
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });

    return () => controller.abort();
  }, [proposalId, reloadKey]);

  function handleRetry() {
    setLoading(true);
    setLoadError(null);
    setReloadKey((key) => key + 1);
  }

  function handleChange(key) {
    return (event) => {
      setValues((current) => ({ ...current, [key]: event.target.value }));
      setResult(null);
      if (errors[key]) setErrors((current) => ({ ...current, [key]: undefined }));
    };
  }

  async function handleSubmit(event) {
    event.preventDefault();
    if (savingRef.current || !proposalId) return;

    const nextErrors = validateSetup(values);
    setErrors(nextErrors);
    if (Object.keys(nextErrors).length > 0) return;

    savingRef.current = true;
    setSaving(true);
    setResult(null);

    try {
      const config = await saveAutomationConfig(proposalId, {
        allowedRepository: values.repository.trim(),
        slackChannelId: values.channelId.trim(),
      });
      setSaved(config);
      setValues(toValues(config));
      setResult({ type: "success" });
      setVerification(null);
      setVerifyError(null);
      setActionResult(null);
    } catch (error) {
      setResult({ type: "error", text: error.message });
    } finally {
      savingRef.current = false;
      setSaving(false);
    }
  }

  async function handleVerify() {
    if (verifying || !proposalId) return;
    setVerifying(true);
    setVerifyError(null);

    try {
      const body = await verifyAutomationConnections(proposalId);
      setVerification(body);
      if (body.data) setSaved(body.data);
    } catch (error) {
      setVerification(null);
      setVerifyError(error.message);
    } finally {
      setVerifying(false);
    }
  }

  async function handleActivate() {
    if (acting || !proposalId) return;
    setActing("activate");
    setActionResult(null);
    setVerifyError(null);

    try {
      const body = await activateAutomation(proposalId);
      setVerification(body);
      if (body.data) setSaved(body.data);
      setActionResult(
        body.activated && body.data?.operational_status === "active"
          ? {
              type: "success",
              title: "Automation active",
              text: "Active for simulated events. Matching pull request merged events will now send a real Slack notification and appear in Execution History.",
            }
          : {
              type: "error",
              title: "Not activated",
              text: "Every check must be verified. See the results above.",
            }
      );
    } catch (error) {
      setActionResult({ type: "error", title: "Not activated", text: error.message });
    } finally {
      setActing(null);
    }
  }

  async function handleDisable() {
    if (acting || !proposalId) return;
    setActing("disable");
    setActionResult(null);

    try {
      const body = await disableAutomation(proposalId);
      if (body.data) setSaved(body.data);
      setActionResult({
        type: "success",
        title: "Automation disabled",
        text: "It stays disabled until it is activated again.",
      });
    } catch (error) {
      setActionResult({ type: "error", title: "Not disabled", text: error.message });
    } finally {
      setActing(null);
    }
  }

  const savedAt = saved && formatDate(saved.updated_at);
  const unchanged =
    saved !== null &&
    values.repository.trim() === saved.allowed_repository &&
    values.channelId.trim() === saved.slack_channel_id;
  const busy = loading || saving || verifying || acting !== null;
  const statusCopy = OPERATIONAL_STATUS[status] ?? OPERATIONAL_STATUS.setup_required;
  const allPassed =
    verification !== null &&
    Object.values(verification.checks ?? {}).every((item) => item.status === "verified");
  const statusDetail =
    status === "active"
      ? `Simulated events only.${formatDate(saved.activated_at) ? ` Activated ${formatDate(saved.activated_at)}.` : ""}`
      : status === "disabled"
        ? `Disabled${formatDate(saved.disabled_at) ? ` ${formatDate(saved.disabled_at)}` : ""}. Activate again to re-run all checks.`
        : "Not active.";

  return (
    <section className="automation-setup" aria-labelledby={titleId} aria-busy={busy}>
      <h3 className="candidate-detail-title" id={titleId}>
        {statusCopy.label}
      </h3>
      <p className="candidate-muted">
        Notify a Slack channel when a pull request is merged in the allowed
        repository.
      </p>

      <dl className="automation-setup-status" aria-live="polite">
        {saved && (
          <div>
            <dt>Status</dt>
            <dd>
              <span className={`status-pill${statusCopy.tone}`}>{statusCopy.label}</span>
              <span className="candidate-muted">{statusDetail}</span>
            </dd>
          </div>
        )}
        <div>
          <dt>Setup</dt>
          <dd>
            {loading ? (
              <span className="candidate-muted">Loading saved setup…</span>
            ) : (
              <>
                <span className="status-pill">{saved ? "Saved" : "Not saved"}</span>
                {savedAt && <span className="candidate-muted">Last saved {savedAt}</span>}
              </>
            )}
          </dd>
        </div>
        {verification ? (
          <>
            {Object.entries(CHECK_LABELS).map(([key, label]) => (
              <div key={key}>
                <dt>{label}</dt>
                <dd>
                  <CheckPill status={verification.checks?.[key]?.status} />
                  <span className="candidate-muted">{verification.checks?.[key]?.detail}</span>
                </dd>
              </div>
            ))}
            <div>
              <dt>GitHub events</dt>
              <dd>
                <span className="status-pill">
                  {verification.github_source?.mode === "live" ? "Live" : "Simulated"}
                </span>
                <span className="candidate-muted">{verification.github_source?.detail}</span>
              </dd>
            </div>
          </>
        ) : (
          <div>
            <dt>Connections</dt>
            <dd>
              <span className="status-pill">Not verified</span>
              <span className="candidate-muted">
                {saved ? "Use Verify connections to check Slack." : "Save setup before verifying."}
              </span>
            </dd>
          </div>
        )}
      </dl>

      {verification && (
        <p className="candidate-muted candidate-footnote">
          {allPassed
            ? status === "active"
              ? "All checks passed."
              : "All checks passed. The automation is not active until you activate it."
            : "Not ready: resolve the failed, unverified or unchecked items above."}
          {formatDate(verification.checked_at) && ` Checked ${formatDate(verification.checked_at)}.`}
        </p>
      )}

      {verifyError && (
        <div className="alert alert-error automation-setup-result" role="alert">
          <Icon name="alertCircle" size={18} className="alert-icon" />
          <div className="alert-body">
            <p className="alert-title">Verification did not run</p>
            <p className="alert-text">{verifyError}</p>
          </div>
        </div>
      )}

      {loadError ? (
        <div className="alert alert-error automation-setup-result" role="alert">
          <Icon name="alertCircle" size={18} className="alert-icon" />
          <div className="alert-body">
            <p className="alert-title">Could not load saved setup</p>
            <p className="alert-text">{loadError}</p>
            <button type="button" className="button button-ghost empty-state-action" onClick={handleRetry}>
              <Icon name="reset" />
              Try again
            </button>
          </div>
        </div>
      ) : (
        <form className="form" onSubmit={handleSubmit} noValidate>
          <fieldset className="form-fields automation-setup-fields" disabled={busy}>
            <FormField
              id={`${idPrefix}-repository`}
              label="Allowed repository"
              hint="GitHub owner/name. Merges in other repositories are ignored."
              error={errors.repository}
            >
              {(aria) => (
                <input
                  id={`${idPrefix}-repository`}
                  className="input mono"
                  type="text"
                  placeholder="owner/repository"
                  autoComplete="off"
                  spellCheck={false}
                  value={values.repository}
                  onChange={handleChange("repository")}
                  required
                  {...aria}
                />
              )}
            </FormField>
            <FormField
              id={`${idPrefix}-channel`}
              label="Slack Channel ID"
              hint="Found in Slack under channel details. Starts with C or G."
              error={errors.channelId}
            >
              {(aria) => (
                <input
                  id={`${idPrefix}-channel`}
                  className="input mono"
                  type="text"
                  placeholder="C0123456789"
                  autoComplete="off"
                  spellCheck={false}
                  value={values.channelId}
                  onChange={handleChange("channelId")}
                  required
                  {...aria}
                />
              )}
            </FormField>
          </fieldset>

          {status === "active" && !unchanged && (
            <p className="candidate-muted candidate-footnote" role="note">
              Saving these changes deactivates the automation until it is
              activated again.
            </p>
          )}

          {result && (
            <div
              className={`alert ${result.type === "error" ? "alert-error" : "alert-success"} automation-setup-result`}
              role={result.type === "error" ? "alert" : "status"}
            >
              <Icon
                name={result.type === "error" ? "alertCircle" : "checkCircle"}
                size={18}
                className="alert-icon"
              />
              <div className="alert-body">
                <p className="alert-title">
                  {result.type === "error" ? "Setup not saved" : "Setup saved"}
                </p>
                <p className="alert-text">
                  {result.type === "error"
                    ? result.text
                    : "The automation is not active. Verify connections, then activate it."}
                </p>
              </div>
            </div>
          )}

          <div className="form-actions automation-setup-actions">
            <button
              type="submit"
              className="button button-ghost"
              disabled={busy || unchanged}
              aria-busy={saving}
            >
              {saving && <span className="spinner" aria-hidden="true" />}
              {saving ? "Saving…" : saved ? "Update setup" : "Save setup"}
            </button>
            <button
              type="button"
              className="button button-ghost"
              disabled={busy || !unchanged}
              aria-busy={verifying}
              title={!unchanged ? "Save the setup before verifying" : undefined}
              onClick={handleVerify}
            >
              {verifying && <span className="spinner" aria-hidden="true" />}
              {verifying ? "Verifying…" : "Verify connections"}
            </button>
            {status === "active" ? (
              <button
                type="button"
                className="button button-ghost"
                disabled={busy}
                aria-busy={acting === "disable"}
                onClick={handleDisable}
              >
                {acting === "disable" && <span className="spinner" aria-hidden="true" />}
                {acting === "disable" ? "Disabling…" : "Disable automation"}
              </button>
            ) : (
              <button
                type="button"
                className="button button-primary"
                disabled={busy || !unchanged}
                aria-busy={acting === "activate"}
                aria-describedby={`${idPrefix}-activate-note`}
                title={!unchanged ? "Save the setup before activating" : undefined}
                onClick={handleActivate}
              >
                {acting === "activate" ? (
                  <span className="spinner" aria-hidden="true" />
                ) : (
                  <Icon name="zap" size={14} />
                )}
                {acting === "activate" ? "Activating…" : "Activate automation"}
              </button>
            )}
          </div>

          {actionResult && (
            <div
              className={`alert ${actionResult.type === "error" ? "alert-error" : "alert-success"} automation-setup-result`}
              role={actionResult.type === "error" ? "alert" : "status"}
            >
              <Icon
                name={actionResult.type === "error" ? "alertCircle" : "checkCircle"}
                size={18}
                className="alert-icon"
              />
              <div className="alert-body">
                <p className="alert-title">{actionResult.title}</p>
                <p className="alert-text">{actionResult.text}</p>
              </div>
            </div>
          )}

          <p className="candidate-muted candidate-footnote" id={`${idPrefix}-activate-note`}>
            Activation re-runs every check on the server. While active, the
            automation automatically processes matching simulated GitHub pull
            request merged events and sends a real Slack notification to the
            approved channel. Results are recorded in Execution History.
          </p>
        </form>
      )}
    </section>
  );
}
