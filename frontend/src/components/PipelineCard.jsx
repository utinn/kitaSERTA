const STEPS = [
  { title: "Creator Dashboard", text: "Validates the activity and sends it to the API." },
  { title: "FastAPI", text: "Normalizes the event and assigns an event ID." },
  { title: "Langflow", text: "Receives the event through its webhook." },
  { title: "Supabase", text: "Stores the activity log for later analysis." },
];

export default function PipelineCard() {
  return (
    <section className="card" aria-labelledby="pipeline-title">
      <div className="card-header">
        <div>
          <h2 className="card-title" id="pipeline-title">
            How submissions flow
          </h2>
          <p className="card-description">
            Each activity passes through the following services.
          </p>
        </div>
      </div>
      <ol className="pipeline">
        {STEPS.map((step, index) => (
          <li className="pipeline-step" key={step.title}>
            <span className="pipeline-index" aria-hidden="true">
              {index + 1}
            </span>
            <div>
              <p className="pipeline-title">{step.title}</p>
              <p className="pipeline-text">{step.text}</p>
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}
