import { useState } from "react";
import Icon from "./Icon.jsx";

function CopyButton({ value }) {
  const [copied, setCopied] = useState(false);

  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard unavailable (e.g. insecure context); the value stays selectable.
    }
  }

  return (
    <button
      type="button"
      className="icon-button icon-button-sm"
      onClick={handleCopy}
      aria-label={copied ? "Event ID copied" : "Copy event ID"}
      title={copied ? "Copied" : "Copy"}
    >
      <Icon name={copied ? "check" : "copy"} size={14} />
    </button>
  );
}

export default function SubmissionResult({ result }) {
  if (result.type === "success") {
    return (
      <div className="alert alert-success" role="status">
        <Icon name="checkCircle" size={18} className="alert-icon" />
        <div className="alert-body">
          <p className="alert-title">Request accepted by Langflow</p>
          {result.message && <p className="alert-text">{result.message}.</p>}

          <div className="event-id">
            <span className="event-id-label">Event ID</span>
            <code className="event-id-value">{result.eventId}</code>
            <CopyButton value={result.eventId} />
          </div>

          <p className="alert-footnote">
            Database persistence is not yet verified.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="alert alert-error" role="alert">
      <Icon name="alertCircle" size={18} className="alert-icon" />
      <div className="alert-body">
        <p className="alert-title">Submission failed</p>
        <p className="alert-text">{result.message}</p>
      </div>
    </div>
  );
}
