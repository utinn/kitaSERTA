"""Tests for dispatching simulated PR merged events to Flow 3.

Run from backend/:  .venv/Scripts/python.exe -m unittest discover -s tests
All Supabase, Langflow and Composio traffic goes to in-memory fakes:
no real flow runs, Slack messages or network calls.
"""

import asyncio
import importlib
import json
import os
import sys
import unittest
from types import SimpleNamespace
from unittest import mock
from urllib.parse import parse_qs

import httpx

from test_automation_config_api import REAL_ASYNC_CLIENT, TEST_ENV, FakeSupabase, candidate
from test_connection_verification_api import COMPOSIO_ENV, FLOW_ID

import automation_config_api  # noqa: E402
import automation_execution  # noqa: E402
import connection_verification_api  # noqa: E402

EVENT_ID = "dashboard-9965ce09-e4fa-4acd-8c89-144cf8a82839"
ACTIVE = {
    "proposal_id": "approved-pr",
    "template_type": "github_pr_merged_to_slack",
    "allowed_repository": "oneforall/frontend",
    "slack_channel_id": "C0123456789",
    "operational_status": "active",
    "event_source_mode": "simulated",
    "activated_at": "2026-10-11T00:00:00+00:00",
    "disabled_at": None,
    "activated_slack_account_id": COMPOSIO_ENV["COMPOSIO_SLACK_CONNECTED_ACCOUNT_ID"],
    "activated_execution_flow_id": FLOW_ID,
    "created_at": "2026-10-10T00:00:00+00:00",
    "updated_at": "2026-10-11T00:00:00+00:00",
}


def event(**overrides):
    return {
        "id": "493e5067-c700-4cc1-aaba-c2e1c1d58d46",
        "employee_id": "dev-002",
        "tool": "github",
        "action": "pull_request_merged",
        "object": "Merge activity logs table UI",
        "project": "oneforall/frontend",
        "timestamp": "2026-10-11T01:00:00+00:00",
        "source_event_id": EVENT_ID,
        "metadata": {"event_id": EVENT_ID, "trigger_slug": "GITHUB_PULL_REQUEST_MERGED"},
        "created_at": "2026-10-11T01:00:01+00:00",
        **overrides,
    }


def chat_output(text):
    return {"outputs": [{"outputs": [{"results": {"message": {"text": text}}}]}]}


class ExecutionFake(FakeSupabase):
    """Adds activity_events, automation_executions, filtered config reads
    and Flow 3 runs to the shared fake."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.events = {}
        self.executions = {}
        self.runs = []
        self.run_response = lambda request: httpx.Response(200, json=chat_output("Notification sent."))
        self.executions_table = True

    def __call__(self, request):
        query = {k: v[0] for k, v in parse_qs(request.url.query.decode()).items()}
        table = request.url.path.rsplit("/", 1)[-1]

        if request.url.host == "langflow.test" and "/run/" in request.url.path:
            self.runs.append({
                "path": request.url.path,
                "body": json.loads(request.content),
                "api_key": request.headers.get("x-api-key"),
            })
            return self.run_response(request)

        if table == "activity_events":
            sid = query.get("source_event_id", "").removeprefix("eq.")
            row = self.events.get(sid)
            return httpx.Response(200, json=[row] if row else [])

        if table == "automation_executions":
            if not self.executions_table:
                return httpx.Response(404, json={"code": "PGRST205"})
            if request.method == "POST":
                body = json.loads(request.content)
                key = (body["proposal_id"], body["source_event_id"])
                if key in self.executions:
                    return httpx.Response(409, json={"code": "23505"})
                row = {"id": f"exec-{len(self.executions) + 1}", **body}
                self.executions[key] = row
                return httpx.Response(201, json=[row])
            if request.method == "PATCH":
                body = json.loads(request.content)
                execution_id = query["id"].removeprefix("eq.")
                status = query["status"].removeprefix("eq.")
                updated = []
                for row in self.executions.values():
                    if row["id"] == execution_id and row["status"] == status:
                        row.update(body)
                        updated.append(row)
                return httpx.Response(200, json=updated)

        if table == "automation_configs" and request.method == "GET" and "proposal_id" not in query:
            filters = {k: v.removeprefix("eq.") for k, v in query.items() if k != "select"}
            rows = [
                dict(row) for row in self.configs.values()
                if all(str(row.get(k)) == v for k, v in filters.items())
            ]
            return httpx.Response(200, json=rows)

        return super().__call__(request)


class EvidenceComposio:
    """Fake Composio: only the read-only Slack history proxy call."""

    def __init__(self, messages=None, ok=True, error=None):
        self.calls = []
        self.messages = messages if messages is not None else []
        self.ok = ok
        self.error = error
        self.tools = SimpleNamespace(proxy=self._proxy)

    def _proxy(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        data = {"ok": True, "messages": self.messages} if self.ok else {"ok": False, "error": "missing_scope"}
        return SimpleNamespace(status=200, data=data, headers={})


class DispatchTest(unittest.TestCase):
    def setUp(self):
        env = mock.patch.dict(os.environ, {**TEST_ENV, **COMPOSIO_ENV})
        env.start()
        self.addCleanup(env.stop)

        self.fake = ExecutionFake([candidate("approved-pr")], flows=[FLOW_ID])
        self.fake.configs["approved-pr"] = dict(ACTIVE)
        self.fake.events[EVENT_ID] = event()

        fake = self.fake
        for target, name, value in [
            (automation_config_api.httpx, "AsyncClient",
             lambda **kw: REAL_ASYNC_CLIENT(transport=httpx.MockTransport(fake), **kw)),
            (automation_execution, "PERSISTENCE_TIMEOUT_SECONDS", 0.05),
            (automation_execution, "PERSISTENCE_POLL_SECONDS", 0.01),
        ]:
            patcher = mock.patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

        self.use_composio(EvidenceComposio(messages=[{"text": f"PR merged ({EVENT_ID})"}]))

    def use_composio(self, fake):
        self.composio = fake
        patcher = mock.patch.object(connection_verification_api, "create_composio_client", lambda: fake)
        patcher.start()
        self.addCleanup(patcher.stop)

    def dispatch(self, source_event_id=EVENT_ID):
        return asyncio.run(automation_execution.dispatch_new_event(source_event_id))

    def execution(self):
        return self.fake.executions.get(("approved-pr", EVENT_ID))

    def assert_not_dispatched(self):
        self.assertEqual(self.fake.runs, [])
        self.assertEqual(self.fake.executions, {})
        self.assertEqual(self.composio.calls, [])

    def test_dispatches_new_matching_event_with_approved_destination(self):
        results = self.dispatch()
        self.assertEqual(results, [{
            "proposal_id": "approved-pr", "source_event_id": EVENT_ID,
            "dispatched": True, "status": "verified",
        }])

        self.assertEqual(len(self.fake.runs), 1)
        run = self.fake.runs[0]
        self.assertEqual(run["path"], f"/api/v1/run/{FLOW_ID}")
        self.assertEqual(run["api_key"], "test-langflow-key")
        flow_input = json.loads(run["body"]["input_value"])
        self.assertEqual(flow_input["approved_destination"], {
            "slack_channel_id": "C0123456789",
            "allowed_repository": "oneforall/frontend",
        })
        self.assertEqual(flow_input["source_event_id"], EVENT_ID)
        self.assertEqual(flow_input["event"]["project"], "oneforall/frontend")
        self.assertEqual(flow_input["event"]["pull_request_title"], "Merge activity logs table UI")
        self.assertEqual(flow_input["event_source_mode"], "simulated")

        row = self.execution()
        self.assertEqual(row["status"], "verified")
        self.assertEqual(row["slack_channel_id"], "C0123456789")
        self.assertEqual(row["execution_flow_id"], FLOW_ID)
        self.assertEqual(row["event_source_mode"], "simulated")
        self.assertEqual(row["outcome_summary"], "Notification sent.")
        self.assertIsNotNone(row["finished_at"])

        [call] = self.composio.calls
        self.assertEqual(call["method"], "GET")
        self.assertTrue(call["endpoint"].endswith("/conversations.history"))
        self.assertEqual(call["parameters"][0]["value"], "C0123456789")

    def test_repository_match_ignores_case(self):
        self.fake.events[EVENT_ID] = event(project="OneForAll/Frontend")
        self.assertTrue(self.dispatch()[0]["dispatched"])

    def test_llm_success_claim_without_evidence_is_unverified(self):
        self.fake.run_response = lambda r: httpx.Response(
            200, json=chat_output('{"status": "completed", "actions_taken": [{"action": "sent"}]}')
        )
        self.use_composio(EvidenceComposio(messages=[{"text": "unrelated message"}]))
        self.assertEqual(self.dispatch()[0]["status"], "unverified")
        self.assertIn("No message", self.execution()["evidence"])

    def test_missing_history_scope_is_unverified(self):
        self.use_composio(EvidenceComposio(ok=False))
        self.assertEqual(self.dispatch()[0]["status"], "unverified")

    def test_evidence_lookup_error_is_unverified(self):
        self.use_composio(EvidenceComposio(error=RuntimeError("boom")))
        self.assertEqual(self.dispatch()[0]["status"], "unverified")

    def test_missing_chat_output_is_recorded(self):
        self.fake.run_response = lambda r: httpx.Response(200, json={"outputs": []})
        self.use_composio(EvidenceComposio())
        self.assertEqual(self.dispatch()[0]["status"], "unverified")
        self.assertIn("no Chat Output", self.execution()["error"])

    def test_timeout_is_unknown_and_not_retried(self):
        def timeout(request):
            raise httpx.ReadTimeout("timed out", request=request)

        self.fake.run_response = timeout
        self.use_composio(EvidenceComposio())
        self.assertEqual(self.dispatch()[0]["status"], "unknown")
        self.assertEqual(self.dispatch()[0]["reason"], "already claimed")
        self.assertEqual(len(self.fake.runs), 1)
        self.assertEqual(self.execution()["status"], "unknown")

    def test_timeout_with_evidence_is_verified(self):
        def timeout(request):
            raise httpx.ReadTimeout("timed out", request=request)

        self.fake.run_response = timeout
        self.assertEqual(self.dispatch()[0]["status"], "verified")

    def test_server_error_is_unknown(self):
        self.fake.run_response = lambda r: httpx.Response(500)
        self.use_composio(EvidenceComposio())
        self.assertEqual(self.dispatch()[0]["status"], "unknown")

    def test_rejected_or_unreachable_flow_is_failed_without_evidence_check(self):
        def refused(request):
            raise httpx.ConnectError("refused", request=request)

        for name, response in {
            "not found": lambda r: httpx.Response(404),
            "unauthorized": lambda r: httpx.Response(401),
            "unreachable": refused,
        }.items():
            with self.subTest(name):
                self.fake.executions.clear()
                self.fake.run_response = response
                self.use_composio(EvidenceComposio())
                self.assertEqual(self.dispatch()[0]["status"], "failed")
                self.assertEqual(self.composio.calls, [])

    def test_duplicate_dispatch_runs_flow_once(self):
        first = self.dispatch()
        second = self.dispatch()
        self.assertTrue(first[0]["dispatched"])
        self.assertEqual(second[0], {
            "proposal_id": "approved-pr", "source_event_id": EVENT_ID,
            "dispatched": False, "reason": "already claimed",
        })
        self.assertEqual(len(self.fake.runs), 1)
        self.assertEqual(len(self.fake.executions), 1)

    def test_concurrent_dispatch_runs_flow_once(self):
        async def both():
            return await asyncio.gather(
                automation_execution.dispatch_new_event(EVENT_ID),
                automation_execution.dispatch_new_event(EVENT_ID),
            )

        results = asyncio.run(both())
        self.assertEqual(sorted(r[0]["dispatched"] for r in results), [False, True])
        self.assertEqual(len(self.fake.runs), 1)

    def test_claim_failure_prevents_flow_run(self):
        self.fake.executions_table = False
        self.assertEqual(self.dispatch()[0]["reason"], "dispatch error")
        self.assertEqual(self.fake.runs, [])

    def test_skips_ineligible_events_and_automations(self):
        cases = {
            "event before activation": lambda: self.fake.events.update(
                {EVENT_ID: event(created_at="2026-10-10T23:59:59+00:00")}),
            "event at activation": lambda: self.fake.events.update(
                {EVENT_ID: event(created_at=ACTIVE["activated_at"])}),
            "other repository": lambda: self.fake.events.update(
                {EVENT_ID: event(project="oneforall/backend")}),
            "slack account changed": lambda: os.environ.update(
                {"COMPOSIO_SLACK_CONNECTED_ACCOUNT_ID": "ca_other"}),
            "flow id changed": lambda: os.environ.update(
                {"LANGFLOW_EXECUTION_FLOW_ID": "4c7a1d3f-7b2e-4a6f-8b62-2d3e4f5a6b7c"}),
            "invalid saved channel": lambda: self.fake.configs["approved-pr"].update(
                {"slack_channel_id": "#general"}),
            "candidate no longer approved": lambda: self.fake.candidates["approved-pr"].update(
                {"review_status": "rejected"}),
        }
        for name, change in cases.items():
            with self.subTest(name), mock.patch.dict(os.environ, {}):
                self.setUp()
                change()
                results = self.dispatch()
                self.assertFalse(results[0]["dispatched"])
                self.assert_not_dispatched()

    def test_skips_events_that_are_not_valid(self):
        cases = {
            "wrong tool": event(tool="gitlab"),
            "wrong action": event(action="pull_request_opened"),
            "wrong trigger": event(metadata={"trigger_slug": "GITHUB_ISSUE_OPENED"}),
            "empty title": event(object=" "),
            "huge title": event(object="x" * 301),
            "bad project": event(project="not a repo"),
        }
        for name, row in cases.items():
            with self.subTest(name):
                self.fake.events[EVENT_ID] = row
                self.assertEqual(self.dispatch(), [])
                self.assert_not_dispatched()

    def test_non_simulated_event_id_is_ignored(self):
        self.fake.events["github-123"] = event(source_event_id="github-123")
        self.assertEqual(self.dispatch("github-123"), [])
        self.assert_not_dispatched()

    def test_unpersisted_event_is_not_dispatched(self):
        del self.fake.events[EVENT_ID]
        self.assertEqual(self.dispatch(), [])
        self.assert_not_dispatched()

    def test_inactive_automations_are_not_dispatched(self):
        for status in ("setup_required", "disabled"):
            with self.subTest(status):
                self.fake.configs["approved-pr"]["operational_status"] = status
                self.assertEqual(self.dispatch(), [])
                self.assert_not_dispatched()

    def test_disabled_between_listing_and_claim_is_not_dispatched(self):
        original = automation_execution.fetch_active_configs

        async def list_then_disable(*args):
            rows = await original(*args)
            self.fake.configs["approved-pr"]["operational_status"] = "disabled"
            return rows

        with mock.patch.object(automation_execution, "fetch_active_configs", list_then_disable):
            results = self.dispatch()
        self.assertEqual(results[0]["reason"], "automation changed before dispatch")
        self.assert_not_dispatched()


class IngestionHookTest(unittest.TestCase):
    """POST /api/activities schedules dispatch only after Flow 1 accepts."""

    def setUp(self):
        env = mock.patch.dict(os.environ, {
            **TEST_ENV,
            **COMPOSIO_ENV,
            "LANGFLOW_WEBHOOK_URL": "http://langflow.test/api/v1/webhook/flow-1",
            "SUPABASE_ANON_KEY": "test-anon",
        })
        env.start()
        self.addCleanup(env.stop)

        with mock.patch("dotenv.load_dotenv"):
            sys.modules.pop("main", None)
            self.main = importlib.import_module("main")

        from fastapi.testclient import TestClient
        self.client = TestClient(self.main.app)
        self.webhook_status = 202
        self.webhook_calls = []

        def webhook(request):
            self.webhook_calls.append(json.loads(request.content))
            return httpx.Response(self.webhook_status)

        patcher = mock.patch.object(
            self.main.httpx,
            "AsyncClient",
            lambda **kw: REAL_ASYNC_CLIENT(transport=httpx.MockTransport(webhook), **kw),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

        self.dispatch = mock.AsyncMock(return_value=[])
        patcher = mock.patch.object(self.main, "dispatch_new_event", self.dispatch)
        patcher.start()
        self.addCleanup(patcher.stop)

    def submit(self):
        return self.client.post("/api/activities", json={
            "employee_id": "dev-002",
            "activity_type": "pr_merged",
            "title": "Merge activity logs table UI",
            "repository": "oneforall/frontend",
        })

    def test_dispatch_scheduled_after_flow_1_accepts(self):
        response = self.submit()
        self.assertEqual(response.status_code, 202)
        event_id = response.json()["event_id"]
        self.assertTrue(automation_execution.SIMULATED_EVENT_ID.fullmatch(event_id))
        self.dispatch.assert_awaited_once_with(event_id)
        self.assertEqual(self.webhook_calls[0]["metadata"]["event_id"], event_id)

    def test_no_dispatch_when_flow_1_rejects(self):
        self.webhook_status = 500
        self.assertEqual(self.submit().status_code, 502)
        self.dispatch.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
