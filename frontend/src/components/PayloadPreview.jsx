export default function PayloadPreview({ payload, submitted }) {
  return (
    <section className="card" aria-labelledby="payload-title">
      <div className="card-header">
        <div>
          <h2 className="card-title" id="payload-title">
            Request payload
          </h2>
          <p className="card-description">
            {submitted
              ? "Body of the last request sent to the API."
              : "Live preview of the JSON body that will be sent."}
          </p>
        </div>
        <span className="tag mono">POST</span>
      </div>
      <pre className="code-block">
        <code>{JSON.stringify(payload, null, 2)}</code>
      </pre>
    </section>
  );
}
