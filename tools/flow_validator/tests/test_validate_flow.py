"""Unit tests for validate_flow.py, using sanitized synthetic fixtures.

Run from tools/flow_validator/:  python -m unittest discover -s tests
No network access, no Langflow, Composio or Slack.
"""

import io
import json
import socket
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import validate_flow  # noqa: E402
from fixtures import FAKE, bob_flow, clone, find, reference_flow, template  # noqa: E402


class FlowCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        for target in ("socket.socket.connect", "socket.create_connection"):
            patcher = mock.patch(target, side_effect=AssertionError("network access attempted"))
            patcher.start()
            self.addCleanup(patcher.stop)

    def write(self, name, flow):
        path = self.dir / name
        text = flow if isinstance(flow, str) else json.dumps(flow)
        path.write_text(text, encoding="utf-8")
        return path

    def run_validator(self, candidate, reference=None):
        ref = self.write("reference.json", reference if reference is not None else reference_flow())
        cand = self.write("candidate.json", candidate)
        return validate_flow.validate(ref, cand)

    def assert_pass(self, result):
        self.assertEqual(result["result"], "PASS", result["reasons"])

    def assert_fail(self, result, *fragments):
        self.assertEqual(result["result"], "FAIL")
        joined = "\n".join(result["reasons"])
        for fragment in fragments:
            self.assertIn(fragment, joined)
        return joined


class PassingTest(FlowCase):
    def test_reference_against_itself(self):
        result = self.run_validator(reference_flow())
        self.assert_pass(result)
        self.assertTrue(all(h["match"] for h in result["code_hashes"]))
        self.assertEqual(len(result["code_hashes"]), 4)

    def test_bob_generated_variant_with_identical_code(self):
        result = self.run_validator(bob_flow())
        self.assert_pass(result)
        self.assertEqual(result["node_mapping"]["MCPTools-Ref02"], "MCPTools-Bob9b")

    def test_docs_urls_placeholders_and_global_variables_allowed(self):
        flow = bob_flow()
        template(flow, "Agent")["api_key"]["value"] = "OPENAI_API_KEY"
        template(flow, "Agent")["system_prompt"]["value"] += (
            "\nhttps://docs.python.org/3/library/ast.html https://api.slack.com/methods/chat.postMessage"
            "\nAPI key: YOUR_API_KEY. Token: <token>. ${SLACK_CHANNEL}"
        )
        self.assert_pass(self.run_validator(flow))

    def test_empty_secret_field_allowed(self):
        flow = bob_flow()
        template(flow, "Agent")["api_key"]["value"] = ""
        self.assert_pass(self.run_validator(flow))


class CodeTest(FlowCase):
    def set_mcp_code(self, code):
        flow = bob_flow()
        template(flow, "MCPTools")["code"]["value"] = code
        return flow

    def test_truncated_mcp_code_fails(self):
        code = template(reference_flow(), "MCPTools")["code"]["value"]
        cut = code[: code.index("    async def _load_tools")]
        self.assertIsNone(validate_flow.parse_error(cut))
        joined = self.assert_fail(self.run_validator(self.set_mcp_code(cut)), "appears truncated")
        self.assertIn("MCPTools", joined)

    def test_one_line_stub_like_first_bob_run_fails(self):
        stub = "from langflow.custom import Component\nclass MCPToolsComponent(Component): pass"
        self.assert_fail(self.run_validator(self.set_mcp_code(stub)), "appears truncated")

    def test_syntax_error_fails(self):
        code = template(reference_flow(), "MCPTools")["code"]["value"].replace("def build_tools(self):", "def build_tools(self)")
        self.assert_fail(self.run_validator(self.set_mcp_code(code)), "invalid Python syntax (line")

    def test_altered_but_valid_code_fails(self):
        code = template(reference_flow(), "MCPTools")["code"]["value"].replace(
            "t.name == self.tool", "t.name != self.tool"
        )
        self.assertIsNone(validate_flow.parse_error(code))
        self.assert_fail(self.run_validator(self.set_mcp_code(code)), "differs from the known-good reference")

    def test_missing_code_fails(self):
        for value in ("", None, "   "):
            with self.subTest(value=value):
                self.assert_fail(self.run_validator(self.set_mcp_code(value)), "component code is missing")

    def test_non_protected_agent_code_change_also_fails(self):
        flow = bob_flow()
        template(flow, "Agent")["code"]["value"] += "\nimport os\n"
        self.assert_fail(self.run_validator(flow), "differs from the known-good reference")

    def test_code_is_never_executed(self):
        marker = self.dir / "executed.txt"
        flow = self.set_mcp_code(f"open({str(marker)!r}, 'w').write('x')\nraise SystemExit(3)\n")
        self.assert_fail(self.run_validator(flow), "MCPTools")
        self.assertFalse(marker.exists())


class GraphTest(FlowCase):
    def test_missing_node_fails(self):
        flow = bob_flow()
        mcp = find(flow, "MCPTools")
        flow["data"]["nodes"].remove(mcp)
        flow["data"]["edges"] = [e for e in flow["data"]["edges"] if e["source"] != mcp["id"]]
        self.assert_fail(self.run_validator(flow), "missing required component: MCPTools")

    def test_missing_chat_output_fails(self):
        flow = bob_flow()
        out = find(flow, "ChatOutput")
        flow["data"]["nodes"].remove(out)
        flow["data"]["edges"] = [e for e in flow["data"]["edges"] if e["target"] != out["id"]]
        self.assert_fail(self.run_validator(flow), "missing required component: ChatOutput")

    def test_missing_required_output_port_fails(self):
        flow = bob_flow()
        find(flow, "ChatOutput")["data"]["node"]["outputs"] = []
        self.assert_fail(self.run_validator(flow), "required output 'message' is missing")

    def test_dangling_edge_fails(self):
        flow = bob_flow()
        flow["data"]["edges"][2]["target"] = "ChatOutput-Gone"
        flow["data"]["edges"][2]["data"]["targetHandle"]["id"] = "ChatOutput-Gone"
        del flow["data"]["edges"][2]["targetHandle"]
        self.assert_fail(self.run_validator(flow), "dangling edge", "disconnected node")

    def test_missing_handles_fail(self):
        flow = bob_flow()
        del flow["data"]["edges"][1]["data"]
        self.assert_fail(self.run_validator(flow), "missing connection handles")

    def test_handle_string_mismatch_fails(self):
        flow = bob_flow()
        flow["data"]["edges"][0]["sourceHandle"] = "garbage"
        self.assert_fail(self.run_validator(flow), "string does not match its data handle")

    def test_wrong_target_field_fails(self):
        flow = bob_flow()
        flow["data"]["edges"][1]["data"]["targetHandle"]["fieldName"] = "not_a_field"
        del flow["data"]["edges"][1]["targetHandle"]
        self.assert_fail(self.run_validator(flow), "target input 'not_a_field' does not exist")

    def test_incompatible_connection_fails(self):
        flow = bob_flow()
        flow["data"]["edges"][1]["data"]["targetHandle"]["inputTypes"] = ["Message"]
        del flow["data"]["edges"][1]["targetHandle"]
        self.assert_fail(self.run_validator(flow), "incompatible connection")

    def test_rewired_connection_fails(self):
        flow = bob_flow()
        e = flow["data"]["edges"][0]
        e["data"]["targetHandle"]["fieldName"] = "system_prompt"
        del e["targetHandle"]
        self.assert_fail(self.run_validator(flow), "missing required connection ChatInput.message -> Agent.input_value",
                         "unexpected connection")

    def test_disconnected_extra_node_fails(self):
        flow = bob_flow()
        extra = clone(find(flow, "MCPTools"))
        extra["id"] = extra["data"]["id"] = "MCPTools-Extra"
        flow["data"]["nodes"].append(extra)
        self.assert_fail(self.run_validator(flow), "disconnected node", "unexpected component not in the reference: MCPTools")

    def test_changed_field_type_fails(self):
        flow = bob_flow()
        template(flow, "Agent")["tools"]["type"] = "str"
        self.assert_fail(self.run_validator(flow), "input field 'tools' changed type")

    def test_unsupported_structure_fails_closed(self):
        for doc in ({"flows": []}, [], {"data": {"nodes": {}, "edges": []}}, {"data": {"nodes": [], "edges": []}}):
            with self.subTest(doc=doc):
                self.assertEqual(self.run_validator(doc)["result"], "FAIL")

    def test_invalid_json_fails(self):
        result = self.run_validator('{"data": {"nodes": [')
        self.assert_fail(result, "candidate is not valid JSON (line 1")

    def test_reference_without_chat_output_is_unsupported(self):
        ref = reference_flow()
        out = find(ref, "ChatOutput")
        ref["data"]["nodes"].remove(out)
        ref["data"]["edges"] = [e for e in ref["data"]["edges"] if e["target"] != out["id"]]
        self.assert_fail(self.run_validator(ref, reference=ref), "unsupported reference")


class SecretsTest(FlowCase):
    def assert_flagged(self, flow, fragment, secret):
        result = self.run_validator(flow)
        self.assert_fail(result, fragment)
        self.assertNotIn(secret, json.dumps(result))

    def test_secret_field_value_fails(self):
        flow = bob_flow()
        template(flow, "Agent")["api_key"].update(value=FAKE["openai"], load_from_db=False)
        self.assert_flagged(flow, "credential value stored in a secret input", FAKE["openai"])

    def test_plain_secret_in_secret_field_fails(self):
        flow = bob_flow()
        template(flow, "Agent")["api_key"].update(value="mysecretvalue123", load_from_db=False)
        self.assert_flagged(flow, "credential value stored in a secret input", "mysecretvalue123")

    def test_tokens_in_prompt_fail(self):
        for key, fragment in (("slack", "Slack token"), ("bearer", "bearer token"), ("openai", "API key")):
            with self.subTest(key=key):
                flow = bob_flow()
                template(flow, "Agent")["system_prompt"]["value"] += f"\nuse {FAKE[key]}"
                self.assert_flagged(flow, fragment, FAKE[key])

    def test_private_mcp_url_fails(self):
        flow = bob_flow()
        template(flow, "MCPTools")["mcp_server"]["value"] = {"name": "composio", "config": {"url": FAKE["mcp_url"]}}
        result = self.run_validator(flow)
        joined = self.assert_fail(result, "private MCP endpoint URL")
        self.assertNotIn("9f8e7d6c", json.dumps(result))
        self.assertIn("host backend.composio.dev", joined)

    def test_token_inside_code_fails(self):
        flow = bob_flow()
        template(flow, "MCPTools")["code"]["value"] += f"\nTOKEN = {FAKE['slack']!r}\n"
        self.assert_flagged(flow, "Slack token", FAKE["slack"])

    def test_hardcoded_destinations_fail(self):
        for key, fragment in (("channel", "hardcoded Slack channel ID"), ("webhook", "Slack webhook URL")):
            with self.subTest(key=key):
                flow = bob_flow()
                template(flow, "Agent")["system_prompt"]["value"] += f"\nPost to {FAKE[key]}"
                self.assert_flagged(flow, fragment, FAKE[key])

    def test_unsanitized_reference_fails(self):
        ref = reference_flow()
        template(ref, "Agent")["system_prompt"]["value"] += f" {FAKE['channel']}"
        result = self.run_validator(reference_flow(), reference=ref)
        self.assert_fail(result, "reference: hardcoded Slack channel ID")


class CliTest(FlowCase):
    def cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = validate_flow.main(list(args))
        return code, out.getvalue(), err.getvalue()

    def test_pass_and_report_schema(self):
        ref = self.write("ref.json", reference_flow())
        cand = self.write("cand.json", bob_flow())
        report = self.dir / "report.json"
        code, out, _ = self.cli("--reference", str(ref), "--candidate", str(cand), "--report", str(report))
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("PASS"))
        self.assertIn("does not prove", out)
        body = json.loads(report.read_text(encoding="utf-8"))
        self.assertEqual(set(body), {
            "validator", "version", "result", "files", "summary", "reasons",
            "checks", "node_mapping", "code_hashes", "disclaimer",
        })
        self.assertEqual(body["files"]["candidate"]["name"], "cand.json")
        self.assertNotIn(str(self.dir), json.dumps(body))

    def test_fail_exit_code_and_json_output(self):
        ref = self.write("ref.json", reference_flow())
        flow = bob_flow()
        template(flow, "MCPTools")["code"]["value"] = "x = 1"
        cand = self.write("cand.json", flow)
        code, out, _ = self.cli("--reference", str(ref), "--candidate", str(cand), "--json")
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)["result"], "FAIL")

    def test_missing_file_is_usage_error(self):
        ref = self.write("ref.json", reference_flow())
        code, _, err = self.cli("--reference", str(ref), "--candidate", str(self.dir / "nope.json"))
        self.assertEqual(code, 2)
        self.assertIn("candidate file not found", err)


if __name__ == "__main__":
    unittest.main()
