"""Tests for activation and disabling, with fake Supabase, Composio and Langflow.

Run from backend/:  .venv/Scripts/python.exe -m unittest discover -s tests
Nothing is executed: no flow runs, Slack messages or real network calls.
"""

import os
import unittest
from unittest import mock

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from test_automation_config_api import (
    READ,
    REAL_ASYNC_CLIENT,
    TEST_ENV,
    VALID,
    WRITE,
    FakeSupabase,
    candidate,
)
from test_connection_verification_api import (
    COMPOSIO_ENV,
    FLOW_ID,
    SAVED,
    FakeComposio,
    account,
)

import automation_activation_api  # noqa: E402
import automation_config_api  # noqa: E402
import connection_verification_api  # noqa: E402

CONFIG = "/api/candidates/{}/automation-config"
ACTIVATE = CONFIG + "/activate"
DISABLE = CONFIG + "/disable"
SECRETS = [
    "test-composio-key", "ca_test_slack", "user-test", "xoxb",
    "test-langflow-key", FLOW_ID, "sk-flow-secret", "test-review",
]


class ActivationTest(unittest.TestCase):
    def setUp(self):
        env = mock.patch.dict(os.environ, {**TEST_ENV, **COMPOSIO_ENV})
        env.start()
        self.addCleanup(env.stop)

        self.fake = FakeSupabase([
            candidate("approved-pr"),
            candidate("pending-pr", review_status="pending_review"),
        ], flows=[FLOW_ID])
        self.fake.configs["approved-pr"] = dict(SAVED)
        self.fake.configs["pending-pr"] = {**SAVED, "proposal_id": "pending-pr"}

        patcher = mock.patch.object(
            automation_config_api.httpx,
            "AsyncClient",
            lambda **kwargs: REAL_ASYNC_CLIENT(transport=httpx.MockTransport(self.fake), **kwargs),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

        app = FastAPI()
        app.include_router(automation_config_api.router)
        app.include_router(automation_activation_api.router)
        self.client = TestClient(app)
        self.use_composio(FakeComposio())

    def use_composio(self, fake):
        self.composio = fake
        patcher = mock.patch.object(
            connection_verification_api, "create_composio_client", lambda: fake
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def post(self, url, proposal_id="approved-pr", expected=200, **kwargs):
        response = self.client.post(url.format(proposal_id), headers=WRITE, **kwargs)
        self.assertEqual(response.status_code, expected, response.text)
        return response.json()

    def stored(self, proposal_id="approved-pr"):
        return self.fake.configs[proposal_id]

    def assert_nothing_executed(self, body):
        self.assertFalse(body["execution_triggered"])
        self.assertTrue(all(method == "GET" for method, _ in self.fake.langflow_requests))
        self.assertTrue(all("/run/" not in path for _, path in self.fake.langflow_requests))
        for name, _ in self.composio.calls:
            self.assertIn(name, {"connected_accounts.get", "tools.proxy"})
        for _, kwargs in [c for c in self.composio.calls if c[0] == "tools.proxy"]:
            self.assertEqual(kwargs["method"], "GET")

    def test_requires_review_token(self):
        for url in (ACTIVATE, DISABLE):
            with self.subTest(url):
                self.assertEqual(self.client.post(url.format("approved-pr")).status_code, 401)
                self.assertEqual(
                    self.client.post(url.format("approved-pr"), headers=READ).status_code, 401
                )
        self.assertEqual(self.stored()["operational_status"], "setup_required")
        self.assertEqual(self.composio.calls, [])

    def test_activates_when_every_check_passes(self):
        body = self.post(ACTIVATE)

        self.assertTrue(body["activated"])
        self.assertEqual({c["status"] for c in body["checks"].values()}, {"verified"})
        self.assertEqual(body["event_source_mode"], "simulated")
        self.assertEqual(body["github_source"]["mode"], "simulated")
        self.assertEqual(body["data"]["operational_status"], "active")
        self.assertEqual(body["data"]["event_source_mode"], "simulated")
        self.assertIsNotNone(body["data"]["activated_at"])
        self.assert_nothing_executed(body)

        row = self.stored()
        self.assertEqual(row["operational_status"], "active")
        self.assertEqual(row["activated_slack_account_id"], "ca_test_slack")
        self.assertEqual(row["activated_execution_flow_id"], FLOW_ID)

        self.assertNotIn("activated_slack_account_id", body["data"])
        text = str(body)
        for secret in SECRETS:
            self.assertNotIn(secret, text)

        self.assertEqual(self.fake.langflow_requests, [("GET", f"/api/v1/flows/{FLOW_ID}")])

    def test_duplicate_activation_is_idempotent(self):
        first = self.post(ACTIVATE)
        second = self.post(ACTIVATE)

        self.assertTrue(second["activated"])
        self.assertEqual(len(self.fake.configs), 2)
        self.assertEqual(self.fake.upserts, [])
        self.assertEqual(first["data"]["activated_at"], second["data"]["activated_at"])
        self.assertEqual(self.stored()["operational_status"], "active")

    def test_failed_verification_does_not_activate(self):
        cases = {
            "slack account expired": FakeComposio(account_result=account(status="EXPIRED")),
            "channel not found": FakeComposio(channel_data={"ok": False, "error": "channel_not_found"}),
            "channel unverified": FakeComposio(channel_data={"ok": True, "channel": {"is_member": False}}),
        }
        for name, fake in cases.items():
            with self.subTest(name):
                self.use_composio(fake)
                body = self.post(ACTIVATE)
                self.assertFalse(body["activated"])
                self.assertEqual(body["data"]["operational_status"], "setup_required")
                self.assertEqual(self.stored()["operational_status"], "setup_required")
                self.assertEqual(self.fake.patches, [])

    def test_execution_flow_must_be_verified(self):
        cases = {
            "missing": ({"LANGFLOW_EXECUTION_FLOW_ID": ""}, "blocked"),
            "invalid": ({"LANGFLOW_EXECUTION_FLOW_ID": "not-a-uuid"}, "failed"),
            "analysis flow": (
                {"LANGFLOW_EXECUTION_FLOW_ID": COMPOSIO_ENV["LANGFLOW_ANALYSIS_FLOW_ID"]},
                "failed",
            ),
            "not in langflow": (
                {"LANGFLOW_EXECUTION_FLOW_ID": "00000000-0000-4000-8000-000000000000"},
                "failed",
            ),
            "langflow refuses": ({"LANGFLOW_API_KEY": "wrong-key"}, "unverified"),
        }
        for name, (env, expected) in cases.items():
            with self.subTest(name), mock.patch.dict(os.environ, env):
                body = self.post(ACTIVATE)
                self.assertEqual(body["checks"]["execution_flow"]["status"], expected)
                self.assertFalse(body["activated"])
                self.assertEqual(self.stored()["operational_status"], "setup_required")

    def test_failed_revalidation_ends_existing_activation(self):
        self.post(ACTIVATE)
        self.use_composio(FakeComposio(account_result=account(status="REVOKED")))

        body = self.post(ACTIVATE)
        self.assertFalse(body["activated"])
        self.assertEqual(body["data"]["operational_status"], "setup_required")
        self.assertIsNone(self.stored()["activated_at"])

    def test_unapproved_candidate_is_not_activated(self):
        body = self.post(ACTIVATE, "pending-pr")
        self.assertFalse(body["activated"])
        self.assertEqual(body["checks"]["candidate"]["status"], "failed")
        self.assertEqual(self.stored("pending-pr")["operational_status"], "setup_required")
        self.assertEqual(self.composio.calls, [])
        self.assertEqual(self.fake.langflow_requests, [])

    def test_missing_setup_is_not_activated(self):
        del self.fake.configs["approved-pr"]
        body = self.post(ACTIVATE)
        self.assertFalse(body["activated"])
        self.assertIsNone(body["data"])
        self.assertNotIn("approved-pr", self.fake.configs)

    def test_setup_edited_during_activation_conflicts(self):
        original = automation_activation_api.run_readiness_checks

        async def checks_then_edit(proposal_id):
            result = await original(proposal_id)
            self.fake.configs[proposal_id]["slack_channel_id"] = "C9999999999"
            return result

        with mock.patch.object(automation_activation_api, "run_readiness_checks", checks_then_edit):
            self.post(ACTIVATE, expected=409)
        self.assertEqual(self.stored()["operational_status"], "setup_required")

    def test_request_body_cannot_force_activation(self):
        self.use_composio(FakeComposio(account_result=account(status="EXPIRED")))
        body = self.post(ACTIVATE, json={
            "activated": True,
            "operational_status": "active",
            "checks": {"slack_account": {"status": "verified"}},
        })
        self.assertFalse(body["activated"])
        self.assertEqual(self.stored()["operational_status"], "setup_required")

    def test_disable_and_reactivate(self):
        self.post(ACTIVATE)

        body = self.post(DISABLE)
        self.assertEqual(body["data"]["operational_status"], "disabled")
        self.assertIsNotNone(body["data"]["disabled_at"])
        self.assertFalse(body["execution_triggered"])
        self.assertIsNone(self.stored()["activated_slack_account_id"])

        again = self.post(DISABLE)
        self.assertEqual(again["data"]["operational_status"], "disabled")
        self.assertEqual(again["data"]["disabled_at"], body["data"]["disabled_at"])

        reactivated = self.post(ACTIVATE)
        self.assertTrue(reactivated["activated"])
        self.assertEqual(reactivated["data"]["operational_status"], "active")
        self.assertIsNone(reactivated["data"]["disabled_at"])

    def test_disable_requires_active_automation(self):
        self.post(DISABLE, expected=409)
        self.assertEqual(self.stored()["operational_status"], "setup_required")
        del self.fake.configs["approved-pr"]
        self.post(DISABLE, expected=404)

    def test_editing_setup_invalidates_activation(self):
        self.post(ACTIVATE)
        response = self.client.put(
            CONFIG.format("approved-pr"),
            json={**VALID, "slack_channel_id": "C9876543210"},
            headers=WRITE,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["operational_status"], "setup_required")
        row = self.stored()
        self.assertEqual(row["operational_status"], "setup_required")
        self.assertIsNone(row["activated_at"])
        self.assertIsNone(row["activated_slack_account_id"])

    def test_server_account_or_flow_change_invalidates_activation(self):
        cases = {
            "slack account": {"COMPOSIO_SLACK_CONNECTED_ACCOUNT_ID": "ca_other_slack"},
            "execution flow": {"LANGFLOW_EXECUTION_FLOW_ID": "4c7a1d3f-7b2e-4a6f-8b62-2d3e4f5a6b7c"},
        }
        for name, env in cases.items():
            with self.subTest(name):
                self.post(ACTIVATE)
                self.assertEqual(self.stored()["operational_status"], "active")
                with mock.patch.dict(os.environ, env):
                    response = self.client.get(CONFIG.format("approved-pr"), headers=READ)
                self.assertEqual(response.json()["data"]["operational_status"], "setup_required")
                self.assertEqual(self.stored()["operational_status"], "setup_required")


if __name__ == "__main__":
    unittest.main()
