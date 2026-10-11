"""Tests for automation setup persistence against an in-memory fake Supabase.

Run from backend/:  .venv/Scripts/python.exe -m unittest discover -s tests
No real Supabase, Langflow, Composio or Slack calls are made.
"""

import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

import automation_config_api  # noqa: E402

REAL_ASYNC_CLIENT = httpx.AsyncClient

TEST_ENV = {
    "SUPABASE_URL": "https://fake.supabase.test",
    "SUPABASE_SECRET_KEY": "test-secret",
    "CANDIDATE_INGEST_TOKEN": "test-ingest",
    "CANDIDATE_REVIEW_TOKEN": "test-review",
}
READ = {"X-Ingest-Token": "test-ingest"}
WRITE = {"X-Review-Token": "test-review"}
URL = "/api/candidates/{}/automation-config"
VALID = {"allowed_repository": "acme/web-app", "slack_channel_id": "C0123456789"}

SAMPLE = json.loads((BACKEND_DIR / "candidate_test.json").read_text(encoding="utf-8"))
EXPLICIT_SAMPLE = {
    **SAMPLE,
    "steps": [
        {"step_number": 1, "action": "Receive the pull request merged event from GitHub", "tool": "github"},
        SAMPLE["steps"][1],
        {"step_number": 3, "action": "Post a Slack message to the team channel", "tool": "slack"},
    ],
    "required_tools": ["github", "slack"],
    "expected_side_effects": ["A Slack message is posted to the team channel on merge"],
}


def candidate(proposal_id, review_status="approved", validation_result="pass", **proposal):
    return {
        "proposal_id": proposal_id,
        "review_status": review_status,
        "validation_result": validation_result,
        "proposal": {**EXPLICIT_SAMPLE, "proposal_id": proposal_id, **proposal},
    }


LANGFLOW_HOST = "langflow.test"
STATUSES = {"setup_required", "active", "disabled"}


class FakeSupabase:
    """In-memory PostgREST for automation tables, plus Langflow flow lookups.

    Mirrors the 001/002 constraints that matter: one row per proposal_id,
    allowed operational statuses, and the active-snapshot check.
    """

    def __init__(self, candidates, table_exists=True, flows=()):
        self.candidates = {c["proposal_id"]: c for c in candidates}
        self.configs = {}
        self.table_exists = table_exists
        self.flows = set(flows)
        self.upserts = []
        self.patches = []
        self.inserts = []
        self.langflow_requests = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == LANGFLOW_HOST:
            return self.langflow(request)

        assert request.headers.get("apikey") == "test-secret"
        query = {k: v[0] for k, v in parse_qs(request.url.query.decode()).items()}
        proposal_id = query.get("proposal_id", "").removeprefix("eq.")
        table = request.url.path.rsplit("/", 1)[-1]

        if table == "automation_candidates" and request.method == "GET":
            row = self.candidates.get(proposal_id)
            return httpx.Response(200, json=[row] if row else [])

        if table == "automation_candidates" and request.method == "POST":
            body = json.loads(request.content)
            self.inserts.append(body)
            if body["proposal_id"] in self.candidates:
                return httpx.Response(409, json={"code": "23505"})
            self.candidates[body["proposal_id"]] = dict(body)
            return httpx.Response(201, json=[body])

        if table == "automation_configs":
            if not self.table_exists:
                return httpx.Response(404, json={"code": "PGRST205"})
            if request.method == "GET":
                row = self.configs.get(proposal_id)
                return httpx.Response(200, json=[dict(row)] if row else [])
            if request.method == "POST":
                body = json.loads(request.content)
                self.upserts.append((query, dict(request.headers), body))
                existing = self.configs.get(body["proposal_id"])
                row = {
                    "created_at": existing["created_at"] if existing else "2026-10-11T00:00:00+00:00",
                    "event_source_mode": "simulated",
                    **(existing or {}),
                    **body,
                }
                return self.store(row)
            if request.method == "PATCH":
                body = json.loads(request.content)
                filters = {
                    key: value.removeprefix("eq.")
                    for key, value in query.items()
                    if key != "select"
                }
                self.patches.append((filters, body))
                updated = []
                for row in list(self.configs.values()):
                    if all(str(row.get(key)) == value for key, value in filters.items()):
                        response = self.store({**row, **body})
                        if response.status_code >= 400:
                            return response
                        updated.append(dict(self.configs[row["proposal_id"]]))
                return httpx.Response(200, json=updated)

        return httpx.Response(500, json={"message": "unexpected request"})

    def store(self, row):
        if row.get("operational_status") not in STATUSES:
            return httpx.Response(400, json={"code": "23514"})
        if row["operational_status"] == "active" and not (
            row.get("activated_at")
            and row.get("activated_slack_account_id")
            and row.get("activated_execution_flow_id")
        ):
            return httpx.Response(400, json={"code": "23514"})
        self.configs[row["proposal_id"]] = row
        return httpx.Response(201, json=[dict(row)])

    def langflow(self, request):
        self.langflow_requests.append((request.method, request.url.path))
        assert request.method == "GET", "Langflow may only be read"
        assert "/run/" not in request.url.path, "Flows must never be run"
        flow_id = request.url.path.rsplit("/", 1)[-1]
        if request.headers.get("x-api-key") != "test-langflow-key":
            return httpx.Response(403)
        if flow_id not in self.flows:
            return httpx.Response(404, json={"detail": "Flow not found"})
        return httpx.Response(200, json={
            "id": flow_id,
            "name": "Flow 3",
            "data": {"nodes": [{"template": {"api_key": "sk-flow-secret"}}]},
        })


class AutomationConfigApiTest(unittest.TestCase):
    def setUp(self):
        env = mock.patch.dict(os.environ, TEST_ENV)
        env.start()
        self.addCleanup(env.stop)

        self.fake = FakeSupabase([
            candidate("approved-pr"),
            candidate("pending-pr", review_status="pending_review"),
            candidate("blocked-pr", validation_result="blocked"),
            candidate(
                "conditional-pr",
                validation_result="conditional",
                blocked_reasons=["SOP owner sign-off pending"],
            ),
            candidate("other-trigger", trigger="GITHUB_ISSUE_OPENED"),
        ])
        self.use_fake(self.fake)

        app = FastAPI()
        app.include_router(automation_config_api.router)
        self.client = TestClient(app)

    def use_fake(self, fake):
        patcher = mock.patch.object(
            automation_config_api.httpx,
            "AsyncClient",
            lambda **kwargs: REAL_ASYNC_CLIENT(transport=httpx.MockTransport(fake), **kwargs),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_requires_tokens(self):
        self.assertEqual(self.client.get(URL.format("approved-pr")).status_code, 401)
        self.assertEqual(
            self.client.put(URL.format("approved-pr"), json=VALID, headers=READ).status_code,
            401,
        )

    def test_get_returns_null_when_not_saved(self):
        response = self.client.get(URL.format("approved-pr"), headers=READ)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"data": None})

    def test_save_then_restore(self):
        response = self.client.put(URL.format("approved-pr"), json=VALID, headers=WRITE)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertFalse(body["execution_triggered"])
        self.assertEqual(body["data"]["template_type"], "github_pr_merged_to_slack")
        self.assertEqual(body["data"]["operational_status"], "setup_required")

        restored = self.client.get(URL.format("approved-pr"), headers=READ).json()["data"]
        self.assertEqual(restored["allowed_repository"], "acme/web-app")
        self.assertEqual(restored["slack_channel_id"], "C0123456789")

    def test_update_does_not_duplicate_and_keeps_created_at(self):
        self.client.put(URL.format("approved-pr"), json=VALID, headers=WRITE)
        created_at = self.fake.configs["approved-pr"]["created_at"]

        response = self.client.put(
            URL.format("approved-pr"),
            json={**VALID, "slack_channel_id": " C9876543210 "},
            headers=WRITE,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self.fake.configs), 1)
        self.assertEqual(self.fake.configs["approved-pr"]["slack_channel_id"], "C9876543210")
        self.assertEqual(self.fake.configs["approved-pr"]["created_at"], created_at)

        query, headers, payload = self.fake.upserts[-1]
        self.assertEqual(query["on_conflict"], "proposal_id")
        self.assertIn("resolution=merge-duplicates", headers["prefer"])
        self.assertNotIn("created_at", payload)

    def test_rejects_unapproved_candidate(self):
        response = self.client.put(URL.format("pending-pr"), json=VALID, headers=WRITE)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.fake.configs, {})

    def test_rejects_sop_blocked_candidate(self):
        response = self.client.put(URL.format("blocked-pr"), json=VALID, headers=WRITE)
        self.assertEqual(response.status_code, 409)
        self.assertIn("SOP", response.json()["detail"])

    def test_warnings_are_not_blockers(self):
        response = self.client.put(URL.format("conditional-pr"), json=VALID, headers=WRITE)
        self.assertEqual(response.status_code, 200)

    def test_rejects_ineligible_template(self):
        response = self.client.put(URL.format("other-trigger"), json=VALID, headers=WRITE)
        self.assertEqual(response.status_code, 422)

    def test_unknown_candidate(self):
        response = self.client.put(URL.format("missing"), json=VALID, headers=WRITE)
        self.assertEqual(response.status_code, 404)

    def test_validates_input_and_forbids_extra_fields(self):
        cases = [
            {**VALID, "slack_channel_id": "#general"},
            {**VALID, "allowed_repository": "acme"},
            {**VALID, "template_type": "something_else"},
            {**VALID, "slack_bot_token": "xoxb-not-allowed"},
        ]
        for payload in cases:
            with self.subTest(payload=payload):
                response = self.client.put(URL.format("approved-pr"), json=payload, headers=WRITE)
                self.assertEqual(response.status_code, 422)
        self.assertEqual(self.fake.configs, {})

    def test_missing_table_reports_migration(self):
        fake = FakeSupabase([candidate("approved-pr")], table_exists=False)
        self.use_fake(fake)
        response = self.client.get(URL.format("approved-pr"), headers=READ)
        self.assertEqual(response.status_code, 503)
        self.assertIn("001_automation_configs.sql", response.json()["detail"])


class EligibilityTest(unittest.TestCase):
    def test_generic_sample_is_not_supported(self):
        self.assertFalse(automation_config_api.is_supported_automation(SAMPLE))
        self.assertTrue(automation_config_api.is_supported_automation(EXPLICIT_SAMPLE))

    def test_matches_frontend_cases(self):
        supported = automation_config_api.is_supported_automation
        self.assertTrue(supported({
            "trigger": "github.pull_request.merged",
            "steps": [{"action": "Post update", "tool": "Slack"}],
        }))
        self.assertFalse(supported({
            "trigger": "GITHUB_PULL_REQUEST_MERGED",
            "required_tools": ["github", "slack"],
            "steps": [],
        }))
        self.assertFalse(supported({
            "trigger": "GITHUB_PULL_REQUEST_MERGED",
            "steps": [{"action": "Send a notification to the team", "tool": "github"}],
        }))
        self.assertFalse(supported({
            "trigger": "GITHUB_PULL_REQUEST_MERGED",
            "steps": [{"action": "Update Jira ticket status", "tool": "jira"}],
        }))
        self.assertFalse(supported({"title": "PR merged Slack notification", "trigger": "none"}))


if __name__ == "__main__":
    unittest.main()
