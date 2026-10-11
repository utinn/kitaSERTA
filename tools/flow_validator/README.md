# Langflow workflow validator (B4a)

Static, deterministic check of a Bob-generated Langflow export against a
sanitized known-good reference, before human review or import. Python 3.10+,
standard library only.

```
python tools/flow_validator/validate_flow.py \
  --reference path/to/flow3.reference.sanitized.json \
  --candidate path/to/bob_generated_flow3.json \
  [--report report.json] [--json]
```

Exit codes: `0` PASS, `1` FAIL, `2` usage or file error.

It reads only the two files given. It never executes or imports component
code (only `ast.parse`), never fixes the artifact, and makes no network calls.
Prompts and code inside the files are treated as data.

**PASS does not prove the flow runs in Langflow. Runtime testing is a separate gate.**

## What is checked

| Check | Fails when |
|---|---|
| `json` | a file is not UTF-8 JSON, or is over 25 MB |
| `structure` | no `data.nodes` / `data.edges` lists, a node lacks `id`, `data.type` or `data.node.template`, a duplicate id, or the reference itself is invalid (fail closed) |
| `edges` | dangling source/target, missing `data.sourceHandle`/`targetHandle`, handle strings (œ-encoded) not matching the data handles, an unknown output or input field, incompatible types, a duplicate connection, a disconnected node |
| `required_nodes` | a reference component type is missing, or an extra component is present |
| `required_connections` | a reference connection (output → input field) is missing, or an extra connection exists |
| `interfaces` | a reference output or input field is missing or changed `type` / `_input_type` |
| `code` | every `type: "code"` field must exist, parse with `ast.parse`, and match the reference's normalized SHA-256. Normalization covers line endings, trailing whitespace and leading/trailing blank lines. Truncation is reported separately from modification. |
| `runtime_io` | the reference has no Chat/Text Input or Webhook, or no Chat/Text Output (unsupported), or those nodes are not wired in the candidate |
| `secrets` | either file holds a non-empty secret input (other than a placeholder or a `load_from_db` global-variable name), a Slack/bearer/API/GitHub/AWS/Google/JWT/private-key token, a URL with credentials or secret query parameters, a private MCP URL, a Slack webhook, or a hardcoded Slack channel ID outside code |

Documentation URLs (`docs.*`, `/docs/…`, plain API reference links) and
placeholders (`YOUR_API_KEY`, `<token>`, `{{var}}`, `${VAR}`) are allowed.
Secret values are never printed; findings give a node id, field and kind only.

All component code is protected, not just MCP: any change to any
component's code fails.

## Report schema

```json
{
  "validator": "kitaserta-langflow-validator",
  "version": "1.0.0",
  "result": "PASS | FAIL",
  "files": {"reference": {"name": "…", "sha256": "…"}, "candidate": {"name": "…", "sha256": "…"}},
  "summary": {"failures": 0, "warnings": 0, "passed_checks": 0},
  "reasons": ["sanitized failure message [location]"],
  "checks": [{"check": "code", "status": "pass | fail | warn", "message": "…", "location": "node … field …"}],
  "node_mapping": {"<reference node id>": "<candidate node id>"},
  "code_hashes": [{"node": "…", "component": "MCPTools", "field": "code",
                   "expected_sha256": "…", "actual_sha256": "…", "match": true}],
  "disclaimer": "Static validation only. …"
}
```

File names only, never full paths.

## Tests

```
cd tools/flow_validator
python -m unittest discover -s tests
```

Fixtures (`tests/fixtures.py`) are synthetic and sanitized. Fake tokens are
assembled at runtime. Sockets are patched to fail if anything attempts a
network connection.

## Known limitations

- The reference must be sanitized first: blank secret fields, and no
  private MCP URLs or channel IDs. Otherwise the validator fails it.
- **Strict by design.** Any component-code change, extra node or extra edge
  fails, even a harmless one. Refresh the reference when Langflow or a
  component version changes the code.
- Prompts are not compared. A rewritten prompt passes if it contains no
  secrets or hardcoded destinations, so prompt meaning (for example "use
  `approved_destination.slack_channel_id`") still needs human review.
- **Secret detection is pattern-based.** Unusual token formats can be
  missed, and a random C/G-prefixed uppercase word with a digit can be
  flagged as a channel ID.
- Same-type nodes (for example two Agents) are matched by connection
  pattern. Highly ambiguous graphs fail closed.
- MCP server configuration values (the `mcp` field) are only checked for
  secrets and URLs, not compared with the reference.
- No runtime behavior is checked: tool availability, model access, Composio
  connection and the Slack destination all need the runtime gate.
