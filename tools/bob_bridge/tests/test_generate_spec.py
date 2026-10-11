"""Unit tests for generate_spec.py with mock Supabase and mock Bob Shell.

Run from tools/bob_bridge/:  python -m unittest discover -s tests
No network access and no real Bob Shell process.
"""

import copy
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import unittest
import urllib.request
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

TOOLS = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(TOOLS / "bob_bridge"))
sys.path.insert(0, str(TOOLS / "flow_validator" / "tests"))

import generate_spec as gs  # noqa: E402
from fixtures import FAKE  # noqa: E402

PID = "proposal-pr-merged-slack"
EXAMPLE = json.loads(gs.EXAMPLE_SPEC.read_text(encoding="utf-8"))
FAKE_SUPABASE_KEY = "sb_secret_" + "Zq8" * 10


def candidate(**proposal_overrides):
    proposal = {
        "proposal_id": PID,
        "title": "Notify Slack when a pull request is merged",
        "department": "Engineering",
        "owner_email": "lead.person@example.com",
        "problem_summary": "Merges go unnoticed.",
        "evidence_event_ids": ["dashboard-9965ce09-e4fa-4acd-8c89-144cf8a82839", "dashboard-20fcaa20-2ed2-4e7c-aca7-5dfafd6eaaea"],
        "trigger": "GITHUB_PULL_REQUEST_MERGED",
        "steps": [
            {"step_number": 1, "action": "Capture the pull_request_merged event", "tool": "github"},
            {"step_number": 2, "action": "Post a Slack message to the team channel", "tool": "slack"},
        ],
        "required_tools": ["github", "slack"],
        "expected_side_effects": ["A Slack message is sent on merge"],
        "sop_status": "present",
        "sop_constraints": {
            "required_fields": ["pull request title"],
            "prohibited_actions": ["never post outside the approved channel"],
            "allowed_actors": ["Some Person"],
        },
        "validation_result": "pass",
        "human_approval_required": True,
        "auto_execute_allowed": False,
        "raw_activity": [{"actor": "someone", "payload": "x" * 50}],
    }
    proposal.update(proposal_overrides)
    return {
        "proposal_id": PID,
        "title": proposal["title"],
        "proposal": proposal,
        "evidence_event_ids": proposal["evidence_event_ids"],
        "validation_result": proposal["validation_result"],
        "sop_status": proposal["sop_status"],
        "review_status": "approved",
    }


def envelope(message, status="success"):
    return json.dumps({"type": "result", "timestamp": "2026-10-11T00:00:00Z", "status": status,
                       "stats": {"duration_ms": 10}, "last_message": message})


def bob_spec(**overrides):
    spec = copy.deepcopy(EXAMPLE)
    spec.update(overrides)
    return spec


class FakeBob:
    """Stands in for run_bob: records calls, returns canned stdout."""

    def __init__(self, stdout=None, error=None):
        self.stdout = stdout if stdout is not None else envelope(json.dumps(bob_spec()))
        self.error = error
        self.calls = []

    def __call__(self, command, prompt, timeout):
        self.calls.append({"command": command, "prompt": prompt, "timeout": timeout})
        if self.error:
            raise self.error
        return self.stdout, {"invoked": True, "exit_code": 0, "timed_out": False, "duration_seconds": 1.0}


class BridgeCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.output = self.dir / "spec.json"
        for target in ("socket.socket.connect", "socket.create_connection", "urllib.request.urlopen"):
            patcher = mock.patch(target, side_effect=AssertionError("network access attempted"))
            patcher.start()
            self.addCleanup(patcher.stop)
        resolve = mock.patch.object(gs, "resolve_bob", return_value=["node", "bob.js"])
        resolve.start()
        self.addCleanup(resolve.stop)

    def generate(self, row=None, bob=None, **kwargs):
        self.bob = bob or FakeBob()
        row = row if row is not None else candidate()
        return gs.generate(PID, self.output, fetch=lambda pid: copy.deepcopy(row), runner=self.bob, **kwargs)

    def assert_blocked(self, report, fragment):
        self.assertEqual(report["result"], "BLOCKED", report)
        self.assertIn(fragment, "\n".join(report["reasons"]))
        self.assertEqual(self.bob.calls, [], "Bob must not be invoked for blocked candidates")
        self.assertFalse(self.output.exists())

    def assert_failed(self, report, stage, fragment):
        self.assertEqual(report["result"], "FAIL", report)
        self.assertEqual(report["stage"], stage)
        self.assertIn(fragment, "\n".join(report["reasons"]))
        self.assertFalse(self.output.exists())
        self.assertFalse(self.output.with_name(self.output.name + ".tmp").exists())


class SuccessTest(BridgeCase):
    def test_approved_candidate_produces_valid_spec(self):
        report = self.generate()
        self.assertEqual(report["result"], "SPEC_VALID", report)
        self.assertEqual(report["technical_readiness"], "not_established")
        written = json.loads(self.output.read_text(encoding="utf-8"))
        self.assertEqual(written, EXAMPLE)
        self.assertEqual(len(self.bob.calls), 1)
        self.assertEqual(report["output"]["name"], "spec.json")

    def test_fenced_reply_accepted(self):
        bob = FakeBob(envelope("```json\n" + json.dumps(bob_spec(), indent=2) + "\n```"))
        self.assertEqual(self.generate(bob=bob)["result"], "SPEC_VALID")

    def test_conditional_and_missing_sop_keep_warnings(self):
        row = candidate(validation_result="conditional", sop_status="missing")
        report = self.generate(row)
        self.assertEqual(report["result"], "SPEC_VALID", report)
        joined = "\n".join(report["warnings"])
        self.assertIn("conditional", joined)
        self.assertIn("SOP status is missing", joined)
        self.assertIn("SOP validation is conditional", self.bob.calls[0]["prompt"])

    def test_context_is_allowlisted_and_redacted(self):
        row = candidate(steps=[
            {"step_number": 1, "action": "Capture merge event from https://internal.example/hook", "tool": "github"},
            {"step_number": 2, "action": f"Post to Slack {FAKE['channel']} with {FAKE['slack']}, cc boss@example.com", "tool": "slack"},
        ])
        self.generate(row)
        prompt = self.bob.calls[0]["prompt"]
        context = json.loads(prompt.split("<candidate>\n", 1)[1].split("\n</candidate>", 1)[0])
        self.assertEqual(set(context), {
            "proposal_id", "title", "trigger", "steps", "required_tools", "evidence_event_ids",
            "validation_result", "sop_status", "sop_constraints", "warnings",
        })
        for leaked in (FAKE["channel"], FAKE["slack"], "boss@example.com", "lead.person@example.com",
                       "internal.example", "Engineering", "Some Person", "raw_activity", "xxxxx"):
            self.assertNotIn(leaked, prompt)
        self.assertNotIn("allowed_actors", context["sop_constraints"])
        self.assertIn("untrusted data", prompt)
        self.assertIn('"template_type": "github_pr_merged_to_slack"', prompt)


class EligibilityTest(BridgeCase):
    def test_blocked_candidates_never_reach_bob(self):
        cases = [
            ("pending review", dict(review_status="pending_review"), {}, "not approved"),
            ("rejected", dict(review_status="rejected"), {}, "not approved"),
            ("sop blocked", dict(validation_result="blocked"), dict(validation_result="blocked"), "blocked by SOP"),
            ("results disagree", dict(validation_result="pass"), dict(validation_result="blocked"), "disagree"),
            ("no evidence", dict(evidence_event_ids=[]), dict(evidence_event_ids=[]), "evidence"),
            ("bad evidence", dict(evidence_event_ids=["<script>"]), dict(evidence_event_ids=["<script>"]), "evidence"),
            ("no trigger", {}, dict(trigger=None), "no trigger"),
            ("other trigger", {}, dict(trigger="GITHUB_ISSUE_OPENED"), "only GITHUB_PULL_REQUEST_MERGED"),
            ("no steps", {}, dict(steps=[]), "no proposed steps"),
        ]
        for label, row_changes, proposal_changes, fragment in cases:
            with self.subTest(label):
                row = candidate(**proposal_changes)
                row.update(row_changes)
                self.assert_blocked(self.generate(row), fragment)

    def test_generic_notification_is_not_converted_to_slack(self):
        row = candidate(
            steps=[
                {"step_number": 1, "action": "Capture pull_request_merged webhook payload from GitHub", "tool": "github"},
                {"step_number": 3, "action": "Post automated notification or update task status in project management/chat tool", "tool": "github"},
            ],
            required_tools=["github"],
            expected_side_effects=["Sending notification messages to team channels upon merge"],
        )
        self.assert_blocked(self.generate(row), "does not explicitly name Slack")

    def test_extra_services_are_blocked(self):
        for label, changes, fragment in [
            ("jira tool", dict(required_tools=["github", "slack", "jira"]), "tools other than GitHub and Slack"),
            ("email step", dict(steps=[{"action": "Send a Slack message and an email", "tool": "slack"}]), "services other than Slack"),
        ]:
            with self.subTest(label):
                self.assert_blocked(self.generate(candidate(**changes)), fragment)

    def test_missing_proposal_json(self):
        row = candidate()
        row["proposal"] = None
        self.assert_blocked(self.generate(row), "no stored proposal")


class BobOutputTest(BridgeCase):
    def test_invalid_outputs_rejected(self):
        spec_text = json.dumps(bob_spec())
        cases = [
            ("no envelope", "not json at all", "no JSON result envelope"),
            ("error status", envelope(spec_text, status="error"), "unsuccessful result"),
            ("empty message", envelope(""), "no final message"),
            ("null message", envelope(None), "no final message"),
            ("prose", envelope("Here is your spec: " + spec_text), "not a JSON object"),
            ("two objects", envelope(spec_text + "\n" + spec_text), "more than one JSON value"),
            ("two fences", envelope(f"```json\n{spec_text}\n```\n```json\n{spec_text}\n```"), "exactly one ```json block"),
            ("malformed", envelope('{"spec_version": "1",'), "not valid JSON"),
            ("duplicate keys", envelope('{"spec_version": "1", "spec_version": "1"}'), "duplicate keys"),
            ("array", envelope("[1, 2]"), "not a JSON object"),
            ("oversized", envelope("{" + " " * 20000 + "}"), "too large"),
        ]
        for label, stdout, fragment in cases:
            with self.subTest(label):
                self.assert_failed(self.generate(bob=FakeBob(stdout)), "parse", fragment)

    def test_schema_violations_rejected(self):
        cases = [
            ("unsupported template", bob_spec(template_type="github_issue_to_jira"), "unsupported template_type"),
            ("extra field", bob_spec(webhook="x"), "unknown field(s): webhook"),
            ("extra tool", bob_spec(tools=["SLACK_SEND_MESSAGE", "GITHUB_MERGE_PULL_REQUEST"]), "extra tools"),
            ("hardcoded channel", bob_spec(destination={
                "allowed_repository": "runtime:approved_destination.allowed_repository",
                "slack_channel_id": FAKE["channel"]}), "hardcoded"),
            ("approval dropped", {k: v for k, v in bob_spec().items() if k != "approval_policy"}, "approval_policy"),
            ("other service in description", bob_spec(description="Also open a Jira ticket for each merge."), "service other than Slack"),
        ]
        for label, spec, fragment in cases:
            with self.subTest(label):
                report = self.generate(bob=FakeBob(envelope(json.dumps(spec))))
                self.assert_failed(report, "validation", fragment)
                self.assertNotIn(FAKE["channel"], json.dumps(report))


class BobProcessTest(BridgeCase):
    """run_bob itself, with subprocess.run mocked (no real process)."""

    def call(self, completed=None, error=None):
        with mock.patch.object(gs.subprocess, "run", side_effect=error, return_value=completed) as run:
            try:
                return gs.run_bob(["node", "bob.js"], "PROMPT", 60), run
            except gs.Stop as stop:
                return stop, run

    def test_command_line_and_isolation(self):
        env = {"SUPABASE_SECRET_KEY": FAKE_SUPABASE_KEY, "COMPOSIO_API_KEY": "c", "LANGFLOW_API_KEY": "l",
               "SLACK_BOT_TOKEN": "s", "CANDIDATE_REVIEW_TOKEN": "r", "PATH": os.environ.get("PATH", ""), "BOB_LOG_LEVEL": "warn"}
        done = subprocess.CompletedProcess([], 0, stdout=envelope("{}").encode(), stderr=b"")
        with mock.patch.dict(os.environ, env, clear=True):
            (stdout, info), run = self.call(done)
        argv = run.call_args.args[0]
        kwargs = run.call_args.kwargs
        self.assertFalse(kwargs["shell"])
        self.assertEqual(kwargs["timeout"], 60)
        self.assertEqual(argv[:2], ["node", "bob.js"])
        for flag in ("--accept-license", "--disable-mcp", "--disable-subagents"):
            self.assertIn(flag, argv)
        joined = " ".join(argv)
        for pair in ("--format json", "--mode ask", "--disable-tool-groups execute", "--max-turns 3"):
            self.assertIn(pair, joined)
        self.assertEqual(argv[-1], "PROMPT")
        self.assertEqual(argv[argv.index("--workspace") + 1], kwargs["cwd"])
        self.assertIn("kitaserta-bob-", kwargs["cwd"])
        self.assertFalse(Path(kwargs["cwd"]).exists(), "temp workspace must be removed")
        self.assertEqual(set(kwargs["env"]), {"PATH", "BOB_LOG_LEVEL"})
        self.assertNotIn(FAKE_SUPABASE_KEY, joined)
        self.assertEqual(info["exit_code"], 0)

    def test_timeout_is_not_retried(self):
        stop, run = self.call(error=subprocess.TimeoutExpired(["node"], 60))
        self.assertIsInstance(stop, gs.Stop)
        self.assertIn("timed out after 60s (not retried)", stop.reason)
        self.assertTrue(stop.info["timed_out"])
        self.assertEqual(run.call_count, 1)

    def test_missing_cli_and_credentials(self):
        stop, _ = self.call(error=FileNotFoundError())
        self.assertIn("could not be started", stop.reason)
        auth = subprocess.CompletedProcess([], 1, stdout=b"", stderr=b"Error: please log in. api key sk-should-not-leak")
        stop, run = self.call(auth)
        self.assertIn("not authenticated", stop.reason)
        self.assertNotIn("sk-should-not-leak", stop.reason)
        self.assertEqual(run.call_count, 1)
        other = subprocess.CompletedProcess([], 3, stdout=b"boom", stderr=b"")
        stop, _ = self.call(other)
        self.assertIn("exited with code 3", stop.reason)

    def test_resolve_bob(self):
        with mock.patch.object(gs.shutil, "which", return_value=None):
            with self.assertRaises(gs.Stop) as ctx:
                REAL_RESOLVE()
        self.assertIn("Bob Shell CLI not found", ctx.exception.reason)

        npm = self.dir / "npm"
        script = npm / "node_modules" / "bobshell" / "dist" / "bob.js"
        script.parent.mkdir(parents=True)
        script.write_text("// stub", encoding="utf-8")
        (npm / "bob.cmd").write_text("@echo off", encoding="utf-8")
        which = {"bob": str(npm / "bob.cmd"), "node": "C:/node/node.exe"}
        with mock.patch.object(gs.shutil, "which", side_effect=which.get), mock.patch.object(gs.os, "name", "nt"):
            self.assertEqual(REAL_RESOLVE(), ["C:/node/node.exe", str(script)])

        with self.assertRaises(gs.Stop) as ctx:
            REAL_RESOLVE(str(self.dir / "missing.js"))
        self.assertIn("--bob-js path does not exist", ctx.exception.reason)


REAL_RESOLVE = gs.resolve_bob


class ReportTest(BridgeCase):
    def test_timeout_report_and_no_output(self):
        error = gs.Stop("FAIL", "bob", "Bob Shell timed out after 180s (not retried)").with_info(
            {"invoked": True, "exit_code": None, "timed_out": True, "duration_seconds": 180.0})
        report = self.generate(bob=FakeBob(error=error))
        self.assert_failed(report, "bob", "timed out")
        self.assertTrue(report["bob"]["timed_out"])

    def test_report_never_contains_secrets_or_raw_output(self):
        with mock.patch.dict(os.environ, {"SUPABASE_SECRET_KEY": FAKE_SUPABASE_KEY}):
            row = candidate(steps=[{"action": f"Post a Slack message using {FAKE['slack']}", "tool": "slack"}])
            report = self.generate(row, bob=FakeBob(envelope("RAW-BOB-TEXT not json")))
        text = json.dumps(report)
        for leaked in (FAKE_SUPABASE_KEY, FAKE["slack"], "RAW-BOB-TEXT"):
            self.assertNotIn(leaked, text)

    def test_existing_output_and_bad_id(self):
        self.output.write_text("keep", encoding="utf-8")
        report = self.generate()
        self.assertEqual(report["stage"], "input")
        self.assertIn("already exists", report["reasons"][0])
        self.assertEqual(self.output.read_text(encoding="utf-8"), "keep")
        self.assertEqual(self.bob.calls, [])
        report = gs.generate("../../etc", self.dir / "x.json", fetch=lambda p: {}, runner=FakeBob())
        self.assertIn("invalid format", report["reasons"][0])

    def test_wrong_row_returned(self):
        row = candidate()
        row["proposal_id"] = "someone-else"
        self.assert_failed(self.generate(row), "fetch", "different candidate")


class SupabaseTest(BridgeCase):
    def test_missing_configuration(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(gs.Stop) as ctx:
                gs.fetch_candidate(PID)
        self.assertIn("not configured", ctx.exception.reason)

    def test_read_only_get_with_header_key(self):
        captured = {}

        class Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def fake_urlopen(request, timeout):
            captured["request"] = request
            return Response(json.dumps([candidate()]).encode())

        env = {"SUPABASE_URL": "https://fake.supabase.test/", "SUPABASE_SECRET_KEY": FAKE_SUPABASE_KEY}
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(gs.urllib.request, "urlopen", fake_urlopen):
            row = gs.fetch_candidate(PID)
        request = captured["request"]
        self.assertEqual(request.get_method(), "GET")
        self.assertIn("/rest/v1/automation_candidates?", request.full_url)
        self.assertIn("proposal_id=eq.proposal-pr-merged-slack", request.full_url)
        self.assertNotIn(FAKE_SUPABASE_KEY, request.full_url)
        self.assertEqual(request.get_header("Apikey"), FAKE_SUPABASE_KEY)
        self.assertEqual(row["proposal_id"], PID)

    def test_not_found_and_http_errors(self):
        env = {"SUPABASE_URL": "https://fake.supabase.test", "SUPABASE_SECRET_KEY": FAKE_SUPABASE_KEY}

        class Empty(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch.object(gs.urllib.request, "urlopen", lambda r, timeout: Empty(b"[]")):
                with self.assertRaises(gs.Stop) as ctx:
                    gs.fetch_candidate(PID)
                self.assertIn("not found", ctx.exception.reason)
            error = gs.urllib.error.HTTPError("u", 401, "secret detail", {}, None)
            with mock.patch.object(gs.urllib.request, "urlopen", side_effect=error):
                with self.assertRaises(gs.Stop) as ctx:
                    gs.fetch_candidate(PID)
                self.assertEqual(ctx.exception.reason, "Supabase returned HTTP 401")


class CliTest(BridgeCase):
    def test_cli_blocked_exit_code(self):
        row = candidate()
        row["review_status"] = "pending_review"
        report_path = self.dir / "report.json"
        with mock.patch.object(gs, "fetch_candidate", lambda pid: row), \
                mock.patch.object(gs, "run_bob", FakeBob()), redirect_stdout(io.StringIO()) as out:
            code = gs.main(["--proposal-id", PID, "--output", str(self.output), "--report", str(report_path)])
        self.assertEqual(code, 1)
        self.assertTrue(out.getvalue().startswith("BLOCKED (eligibility)"))
        body = json.loads(report_path.read_text(encoding="utf-8"))
        self.assertEqual(set(body), {
            "tool", "version", "result", "stage", "proposal_id", "template_type", "reasons", "warnings",
            "technical_readiness", "bob", "context", "output", "disclaimer",
        })
        self.assertFalse(body["bob"]["invoked"])


if __name__ == "__main__":
    unittest.main()
