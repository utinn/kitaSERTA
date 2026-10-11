import Icon from "./Icon.jsx";

const STEPS = [
  {
    icon: "upload",
    title: "Capture Activity",
    text: "Digital work events are normalized and stored in activity history.",
  },
  {
    icon: "search",
    title: "Discover Patterns",
    text: "AI analyzes stored activity history when analysis is requested to identify potential repetitive workflows.",
  },
  {
    icon: "userCheck",
    title: "Review Opportunities",
    text: "Users review AI-generated recommendations, supporting evidence, risks, and approval requirements.",
  },
  {
    icon: "zap",
    title: "Automate with Control",
    text: "Supported workflows can be configured, verified, and activated after human approval. Matching future events can then trigger the configured automation.",
  },
];

export default function PipelineCard({ style }) {
  return (
    <section className="card reveal" style={style} aria-labelledby="pipeline-title">
      <div className="card-header">
        <div>
          <h2 className="card-title" id="pipeline-title">
            How kitaSERTA Works
          </h2>
          <p className="card-description">
            From everyday work to reviewed, controlled automation.
          </p>
        </div>
      </div>

      <ol className="pipeline">
        {STEPS.map((step, index) => (
          <li
            className="pipeline-step reveal"
            key={step.title}
            style={{ "--reveal-index": index + 3 }}
          >
            <span className="pipeline-index" aria-hidden="true">
              <Icon name={step.icon} size={14} />
            </span>
            <div>
              <p className="pipeline-eyebrow">Step {index + 1}</p>
              <p className="pipeline-title">{step.title}</p>
              <p className="pipeline-text">{step.text}</p>
            </div>
          </li>
        ))}
      </ol>

      <div className="pipeline-note">
        <Icon name="shield" size={16} className="pipeline-note-icon" />
        <p>
          <strong>You stay in control.</strong> Nothing is automated until a
          person reviews and approves it.
        </p>
      </div>
      <p className="pipeline-footnote">
        In this demo, activities entered here simulate GitHub events.
      </p>
    </section>
  );
}
