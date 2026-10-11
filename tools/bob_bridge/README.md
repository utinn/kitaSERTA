# Bob bridge (B4c Phase 1)

Read-only PoC: one approved automation candidate → IBM Bob Shell → a
specification validated by the existing B4b schema
(`tools/flow_builder/build_flow.py`). Python 3.10+, standard library only.

```
python tools/bob_bridge/generate_spec.py --proposal-id PROPOSAL_ID --output spec.json \
  [--report report.json] [--force] [--timeout 180] [--bob-js PATH]
```

Exit codes: `0` SPEC_VALID, `1` BLOCKED or FAIL, `2` usage error.

It never writes to Supabase. It doesn't build, import, activate or run a
workflow, and doesn't call Slack. Bob is called **at most once** per run,
with no retries. A SPEC_VALID result is **not** proof of technical
readiness.

## Input contract

- `--proposal-id`: `automation_candidates.proposal_id`, matching
  `^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$`.
- Environment: `SUPABASE_URL` and `SUPABASE_SECRET_KEY`. These are the
  same variables the backend uses, sent as the `apikey` header in a single
  `GET /rest/v1/automation_candidates`.
- Bob Shell must already be authenticated. On Windows the npm `bob.cmd`
  shim is bypassed: the bridge runs `node …/bobshell/dist/bob.js` directly,
  so untrusted text never passes through `cmd.exe`.

## Pipeline

1. **Fetch** the columns `proposal_id`, `title`, `proposal`,
   `evidence_event_ids`, `validation_result`, `sop_status` and
   `review_status`.
2. **Eligibility.** Any of these means **BLOCKED**, and Bob is not called:
   - `review_status` is not `approved`;
   - `validation_result` is `blocked`, missing, or disagrees with the
     stored proposal;
   - there are no valid evidence IDs;
   - the trigger is missing, or isn't `GITHUB_PULL_REQUEST_MERGED`;
   - there are no steps;
   - Slack isn't named explicitly in `required_tools`, a step tool or step
     text. Generic "notify / chat tool" proposals are **not** converted to
     Slack.
   - tools other than GitHub and Slack are used, or other services (Jira,
     email, Teams…) are mentioned.

   A `conditional` result and a non-`present` SOP status don't block, but
   become warnings in both the report and Bob's context.
3. **Context sent to Bob** (allowlisted):
   - fields: `proposal_id`, `title`, `trigger`, `steps` (action and tool),
     `required_tools`, `evidence_event_ids`, `validation_result`,
     `sop_status`, `warnings`;
   - from `sop_constraints`, only `required_fields`, `mandatory_sequence`,
     `approval_gates`, `prohibited_actions`, `timing_limits` and
     `escalation_conditions`;
   - bounds: strings of at most 300 characters, at most 10 items per list
     and 20 evidence IDs;
   - URLs, tokens, emails and channel IDs are redacted.

   Department, actors, owners, raw activity and other metadata are never
   sent. The prompt embeds the B4b example spec and marks the candidate as
   untrusted data.
4. **Bob call:**
   - command: `bob run --accept-license --format json --mode ask --disable-mcp --disable-subagents --disable-tool-groups execute --max-turns 3 --workspace <temp dir> <prompt>`;
   - runs with `shell=False`, no stdin and a timeout (default 180 s);
   - the working directory is a temporary folder, deleted afterwards;
   - the child environment drops every variable whose name contains
     `SUPABASE`, `COMPOSIO`, `LANGFLOW`, `SLACK`, `CANDIDATE_`,
     `GITHUB_TOKEN` or `OPENAI`.
5. **Parse.**
   - Bob's output must contain a `{"type": "result", "status": "success",
     …, "last_message": …}` line (the `--format json` envelope of Bob Shell
     2.0.6).
   - `last_message` must be exactly one JSON object, or exactly one
     ```json fence.
   - Rejected: prose, a second object, duplicate keys, more than 16 000
     characters of message or 2 MB of output.
6. **Validate** with `build_flow.validate_spec` (B4b, unchanged). The spec
   must also stay faithful to the candidate: same trigger, and no other
   services in `name` or `description`.
7. **Write** the spec atomically, only on success. An existing output is
   kept unless `--force` is passed.

## Output contract

The specification file has exactly the B4b schema; see
`tools/flow_builder/examples/github_pr_merged_to_slack.spec.json`. The
report (`--report`):

```json
{
  "tool": "kitaserta-bob-bridge", "version": "1.0.0",
  "result": "SPEC_VALID | BLOCKED | FAIL",
  "stage": "input | fetch | eligibility | context | bob | parse | validation | write | done",
  "proposal_id": "…",
  "template_type": "github_pr_merged_to_slack",
  "reasons": ["sanitized reason"],
  "warnings": ["SOP status is missing; …"],
  "technical_readiness": "not_established",
  "bob": {"invoked": true, "exit_code": 0, "timed_out": false, "duration_seconds": 12.3},
  "context": {"fields": ["…"], "characters": 4200, "sha256": "…"},
  "output": {"name": "spec.json", "sha256": "…"} | null,
  "disclaimer": "…"
}
```

The report never includes Bob's raw output, stderr, prompt text, secrets
or full paths.

## Tests

```
cd tools/bob_bridge && python -m unittest discover -s tests
```

Supabase and Bob are mocked, and sockets and `urlopen` are patched to fail
on any network attempt.

## One real local integration test (manual)

Do this in PowerShell from the repository root.

1. **Pick a candidate.** Find an approved `proposal_id` on the Execution
   Audit page. It must name Slack explicitly and must not be SOP-blocked;
   otherwise the expected result is BLOCKED.
2. **Load the two Supabase variables** into this session from your own
   secret store, without echoing them:
   ```powershell
   $env:SUPABASE_URL = Read-Host "Supabase URL"
   $s = Read-Host "Supabase secret key" -AsSecureString
   $env:SUPABASE_SECRET_KEY = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
     [Runtime.InteropServices.Marshal]::SecureStringToBSTR($s)); Remove-Variable s
   ```
3. **Run the bridge once:**
   ```powershell
   python tools/bob_bridge/generate_spec.py --proposal-id <PROPOSAL_ID> `
     --output "$env:TEMP\kitaserta-spec.json" --report "$env:TEMP\kitaserta-spec-report.json"
   ```
4. **On SPEC_VALID, build and validate offline** with the sanitized
   reference:
   ```powershell
   python tools/flow_builder/build_flow.py --reference "$HOME\Downloads\reference_flow3.sanitized.json" `
     --spec "$env:TEMP\kitaserta-spec.json" --output "$env:TEMP\kitaserta-flow.json" --report "$env:TEMP\kitaserta-build.json"
   ```
5. **Clear the variables:**
   ```powershell
   Remove-Item Env:SUPABASE_URL, Env:SUPABASE_SECRET_KEY
   ```

## Limitations

- The eligibility rules are deliberately narrow. A real candidate whose
  notification step says "chat tool" instead of Slack is BLOCKED; the
  candidate has to name Slack explicitly.
- Bob's envelope format was taken from Bob Shell 2.0.6. Another version
  may need a parser change, and that fails safely as FAIL (parse).
- Spending is limited only by `--max-turns 3` and the timeout; no
  `--max-cost` is set.
- The child-environment denylist is name-based. Other secrets in your
  shell environment are passed to Bob unless their names contain a listed
  part.
- Not integrated with FastAPI. `generate()` takes `fetch`/`runner` so a
  later API route can reuse it.
