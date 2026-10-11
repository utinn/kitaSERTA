"""Tests for the read-only execution history endpoint, with a fake Supabase.

Run from backend/:  .venv/Scripts/python.exe -m unittest discover -s tests
No network calls: no Slack, Composio or Langflow.
"""

import json
import os
import unittest
from unittest import mock

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from test_automation_config_api import READ, REAL_ASYNC_CLIENT, TEST_ENV, WRITE

import execution_history_api  # noqa: E402

URL = "/api/candidates/{}/executions"
FLOW_ID = "11111111-2222-4333-8444-555555555555"


def execution(proposal_id, n, status="verified", **overrides):
    row = {
        "id": f"aaaaaaaa-0000-4000-8000-{n:012d}",
        "proposal_id": proposal_id,
        "source_event_id": f"dashboard-00000000-0000-4000-8000-{n:012d}",
        "status": status,
        "event_source_mode": "simulated",
        "execution_flow_id": FLOW_ID,
        "slack_channel_id": "C0123456789",
        "allowed_repository": "acme/web-app",
        "outcome_summary": "Posted to C0123456789 via https://mcp.composio.test/secret-session",
        "evidence": "Message with source_event_id found in the approved channel.",
        "error": None,
        "claimed_at": f"2026-10-0{1 + n % 9}T10:00:{n:02d}+00:00",
        "finished_at": f"2026-10-0{1 + n % 9}T10:00:{n + 4:02d}.5+00:00",
    }
    row.update(overrides)
    return row


class FakeExecutions:
    """Applies the PostgREST eq/select/order/limit params the endpoint uses."""

    def __init__(self, rows):
        self.rows = rows
        self.requests = []
        self.fail_with = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append((request.method, request.url.path))
        if self.fail_with:
            return self.fail_with
        assert request.method == "GET"
        assert request.url.path == "/rest/v1/automation_executions"
        params = request.url.params
        proposal_id = params["proposal_id"].removeprefix("eq.")
        rows = [r for r in self.rows if r["proposal_id"] == proposal_id]
        assert params["order"] == "claimed_at.desc"
        rows.sort(key=lambda r: r["claimed_at"], reverse=True)
        rows = rows[: int(params["limit"])]
        fields = params["select"].split(",")
        return httpx.Response(200, json=[{k: r.get(k) for k in fields} for r in rows])


class ExecutionHistoryTest(unittest.TestCase):
    def setUp(self):
        env = mock.patch.dict(os.environ, TEST_ENV)
        env.start()
        self.addCleanup(env.stop)

        self.fake = FakeExecutions([
            execution("approved-pr", 1),
            execution("approved-pr", 3, status="unverified", evidence=(
                "Slack history unavailable (missing_scope)."
            )),
            execution("approved-pr", 2, status="unknown", evidence=None, error=(
                "Flow 3 call ended without a response (ReadTimeout)."
            )),
            execution("other-pr", 9),
        ])
        patcher = mock.patch.object(
            execution_history_api.httpx,
            "AsyncClient",
            lambda **kw: REAL_ASYNC_CLIENT(transport=httpx.MockTransport(self.fake), **kw),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

        app = FastAPI()
        app.include_router(execution_history_api.router)
        self.client = TestClient(app)

    def get(self, proposal_id="approved-pr", expected=200, **params):
        response = self.client.get(URL.format(proposal_id), headers=READ, params=params)
        self.assertEqual(response.status_code, expected, response.text)
        return response.json()

    def test_requires_ingest_token(self):
        for headers in ({}, {"X-Ingest-Token": "wrong"}, WRITE):
            with self.subTest(headers=headers):
                response = self.client.get(URL.format("approved-pr"), headers=headers)
                self.assertEqual(response.status_code, 401)
        self.assertEqual(self.fake.requests, [])

    def test_returns_only_this_proposal_newest_first(self):
        body = self.get()
        self.assertEqual(
            [r["source_event_id"][-1] for r in body["data"]], ["3", "2", "1"]
        )
        self.assertFalse(body["has_more"])
        self.assertEqual(body["limit"], 20)

    def test_empty_history(self):
        body = self.get("no-runs-pr")
        self.assertEqual(body, {"data": [], "limit": 20, "has_more": False})

    def test_bounded_with_has_more(self):
        self.fake.rows = [execution("approved-pr", n) for n in range(1, 30)]
        body = self.get()
        self.assertEqual(len(body["data"]), 20)
        self.assertTrue(body["has_more"])
        body = self.get(limit=5)
        self.assertEqual(len(body["data"]), 5)
        self.get(limit=51, expected=422)
        self.get(limit=0, expected=422)

    def test_response_is_allowlisted_and_sanitized(self):
        body = self.get()
        expected_keys = {
            "source_event_id", "status", "event_source_mode", "allowed_repository",
            "claimed_at", "finished_at", "duration_seconds", "summary", "details",
        }
        for record in body["data"]:
            self.assertEqual(set(record), expected_keys)
        text = json.dumps(body)
        for leaked in (
            "mcp.composio", "secret-session", "C0123456789", FLOW_ID,
            "missing_scope", "ReadTimeout", "aaaaaaaa-0000",
            "test-secret",
        ):
            self.assertNotIn(leaked, text)
        select = self.fake_select()
        for column in ("outcome_summary", "slack_channel_id", "execution_flow_id", "id"):
            self.assertNotIn(column, select.split(","))

    def fake_select(self):
        captured = {}

        def capture(request):
            captured["select"] = request.url.params["select"]
            return httpx.Response(200, json=[])

        with mock.patch.object(
            execution_history_api.httpx,
            "AsyncClient",
            lambda **kw: REAL_ASYNC_CLIENT(transport=httpx.MockTransport(capture), **kw),
        ):
            self.get()
        return captured["select"]

    def test_summaries_never_claim_unconfirmed_success(self):
        records = {r["status"]: r for r in self.get()["data"]}
        self.assertIn("found in the approved channel", records["verified"]["summary"])
        self.assertIn("could not be confirmed", records["unverified"]["summary"])
        self.assertEqual(
            records["unverified"]["details"],
            ["Slack message history could not be checked."],
        )
        self.assertIn("may or may not", records["unknown"]["summary"])
        self.assertEqual(
            records["unknown"]["details"],
            ["Flow 3 call ended without a response (timeout or dropped connection)."],
        )

    def test_unrecognized_text_and_status_are_not_shown(self):
        self.fake.rows = [
            execution("approved-pr", 1, status="unverified", evidence="xoxb-leaked token", error="raw <tool> dump"),
            execution("approved-pr", 2, status="sent_ok"),
            execution("approved-pr", 3, status="claimed", finished_at=None, evidence=None),
        ]
        records = self.get()["data"]
        by_id = {r["source_event_id"][-1]: r for r in records}
        self.assertEqual(by_id["1"]["details"], [])
        self.assertNotIn("xoxb", json.dumps(records))
        self.assertEqual(by_id["2"]["status"], "unknown")
        self.assertIsNone(by_id["3"]["duration_seconds"])
        self.assertIsNone(by_id["3"]["finished_at"])

    def test_known_error_details_keep_only_status_codes(self):
        self.fake.rows = [
            execution("approved-pr", 1, status="failed", evidence=None,
                      error="Langflow rejected the Flow 3 request (HTTP 404)."),
            execution("approved-pr", 2, status="unknown", evidence=None,
                      error="Flow 3 returned HTTP 500."),
        ]
        by_id = {r["source_event_id"][-1]: r for r in self.get()["data"]}
        self.assertEqual(by_id["1"]["details"], ["Langflow rejected the Flow 3 request (HTTP 404)."])
        self.assertEqual(by_id["2"]["details"], ["Flow 3 returned HTTP 500."])

    def test_duration(self):
        record = self.get()["data"][-1]
        self.assertEqual(record["duration_seconds"], 4.5)

    def test_missing_table_and_database_errors(self):
        self.fake.fail_with = httpx.Response(404, json={"code": "PGRST205", "message": "secret detail"})
        body = self.get(expected=503)
        self.assertIn("003_automation_executions.sql", body["detail"])

        self.fake.fail_with = httpx.Response(500, json={"message": "db password=hunter2"})
        body = self.get(expected=502)
        self.assertEqual(body["detail"], "Failed to fetch execution history")

        self.fake.fail_with = httpx.Response(200, json={"not": "a list"})
        self.get(expected=502)

    def test_is_strictly_read_only(self):
        self.get()
        self.get("no-runs-pr")
        self.assertTrue(self.fake.requests)
        self.assertEqual(
            set(self.fake.requests), {("GET", "/rest/v1/automation_executions")}
        )

    def test_write_methods_not_allowed(self):
        for method in ("post", "put", "patch", "delete"):
            response = getattr(self.client, method)(URL.format("approved-pr"), headers=WRITE)
            self.assertEqual(response.status_code, 405)
        self.assertEqual(self.fake.requests, [])


if __name__ == "__main__":
    unittest.main()
