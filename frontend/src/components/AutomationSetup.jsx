import { useId, useState } from "react";
import FormField from "./FormField.jsx";
import Icon from "./Icon.jsx";
import { validateSetup } from "../utils/automationSetup.js";

const EMPTY = { repository: "", channelId: "" };

/**
 * Setup form for an approved, supported proposal.
 * Nothing is saved or sent: there is no backend endpoint for setup,
 * connection verification or activation yet, so activation stays disabled.
 */
export default function AutomationSetup({ proposalId }) {
  const [values, setValues] = useState(EMPTY);
  const [errors, setErrors] = useState({});
  const [checked, setChecked] = useState(false);
  const titleId = useId();
  const idPrefix = `setup-${proposalId ?? titleId}`;

  function handleChange(key) {
    return (event) => {
      setValues((current) => ({ ...current, [key]: event.target.value }));
      setChecked(false);
      if (errors[key]) setErrors((current) => ({ ...current, [key]: undefined }));
    };
  }

  function handleSubmit(event) {
    event.preventDefault();
    const nextErrors = validateSetup(values);
    setErrors(nextErrors);
    setChecked(Object.keys(nextErrors).length === 0);
  }

  return (
    <section className="automation-setup" aria-labelledby={titleId}>
      <h3 className="candidate-detail-title" id={titleId}>
        Automation setup
      </h3>
      <p className="candidate-muted">
        Notify a Slack channel when a pull request is merged in the allowed
        repository.
      </p>

      <form className="form" onSubmit={handleSubmit} noValidate>
        <div className="form-fields automation-setup-fields">
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
        </div>

        {checked && (
          <div className="alert automation-setup-result" role="status">
            <Icon name="checkCircle" size={18} className="alert-icon" />
            <div className="alert-body">
              <p className="alert-title">Setup details look valid</p>
              <p className="alert-text">
                They are not saved yet, and the GitHub and Slack connections
                have not been verified.
              </p>
            </div>
          </div>
        )}

        <div className="form-actions automation-setup-actions">
          <button type="submit" className="button button-ghost">
            Check details
          </button>
          <button
            type="button"
            className="button button-primary"
            disabled
            aria-describedby={`${idPrefix}-activate-note`}
          >
            <Icon name="zap" size={14} />
            Activate automation
          </button>
        </div>
        <p className="candidate-muted candidate-footnote" id={`${idPrefix}-activate-note`}>
          Activation is unavailable until connection verification and the
          activation service are in place.
        </p>
      </form>
    </section>
  );
}
