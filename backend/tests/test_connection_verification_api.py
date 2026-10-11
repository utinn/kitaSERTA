"""Tests for read-only connection verification with fake Supabase and Composio.

Run from backend/:  .venv/Scripts/python.exe -m unittest discover -s tests
No real Supabase, Composio, Slack or Langflow calls are made.
"""

import os
import unittest
from types import SimpleNamespace
from unittest import mock

import httpx
from composio_client import NotFoundError
from fastapi import FastAPI
from fastapi.testclient import TestClient

from test_automation_config_api import (
    REAL_ASYNC_CLIENT,
    TEST_ENV,
    VALID,
    WRITE,
    FakeSupabase,
    candidate,
)

import automation_config_api  # noqa: E402
import connection_verification_api  # noqa: E402

COMPOSIO_ENV = {
    "COMPOSIO_API_KEY": "test-composio-key",
    "COMPOSIO_SLACK_CONNECTED_ACCOUNT_ID": "ca_test_slack",
    "COMPOSIO_USER_ID": "user-test",
    "LANGFLOW_URL": "http://langflow.test",
    "LANGFLOW_API_KEY": "test-langflow-key",
    "LANGFLOW_EXECUTION_FLOW_ID": "3b6f0c2e-6a1d-4f5e-9a51-1c2d3e4f5a6b",
    "LANGFLOW_ANALYSIS_FLOW_ID": "9d1e2f3a-4b5c-4d6e-8f70-81a2b3c4d5e6",
}
FLOW_ID = COMPOSIO_ENV["LANGFLOW_EXECUTION_FLOW_ID"]
VERIFY = "/api/candidates/{}/automation-config/verify"
SAVED = {
    "proposal_id": "approved-pr",
    "template_type": "github_pr_merged_to_slack",
    "allowed_repository": VALID["allowed_repository"],
    "slack_channel_id": VALID["slack_channel_id"],
    "operational_status": "setup_required",
    "created_at": "2026-10-11T00:00:00+00:00",
    "updated_at": "2026-10-11T00:00:00+00:00",
}


def account(status="ACTIVE", toolkit="slack", user_id="user-test", is_disabled=False):
    return SimpleNamespace(
        status=status,
        toolkit=SimpleNamespace(slug=toolkit),
        user_id=user_id,
        is_disabled=is_disabled,
        state={"val": {"access_token": "xoxb-secret-token"}},
    )


def not_found():
    request = httpx.Request("GET", "https://backend.composio.test")
    return NotFoundError("not found", response=httpx.Response(404, request=request), body=None)


class FakeComposio:
    """Exposes only read operations; anything else raises AttributeError."""

    def __init__(self, account_result=None, channel_data=None, proxy_error=None):
        self.calls = []
        self._account = account_result if account_result is not None else account()
        self._channel = channel_data if channel_data is not None else {
            "ok": True,
            "channel": {"id": "C0123456789", "name": "eng", "is_member": True},
        }
        self._proxy_error = proxy_error
        self.connected_accounts = SimpleNamespace(get=self._get_account)
        self.tools = SimpleNamespace(proxy=self._proxy)

    def _get_account(self, account_id):
        self.calls.append(("connected_accounts.get", account_id))
        if isinstance(self._account, Exception):
            raise self._account
        return self._account

    def _proxy(self, **kwargs):
        self.calls.append(("tools.proxy", kwargs))
        if self._proxy_error:
            raise self._proxy_error
        return SimpleNamespace(status=200, data=self._channel, headers={})


class ConnectionVerificationTest(unittest.TestCase):
    def setUp(self):
        env = mock.patch.dict(os.environ, {**TEST_ENV, **COMPOSIO_ENV})
        env.start()
        self.addCleanup(env.stop)

        self.fake = FakeSupabase([
            candidate("approved-pr"),
            candidate("pending-pr", review_status="pending_review"),
            candidate("unsaved-pr"),
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
        app.include_router(connection_verification_api.router)
        self.client = TestClient(app)
        self.use_composio(FakeComposio())

    def use_composio(self, fake):
        self.composio = fake
        patcher = mock.patch.object(
            connection_verification_api, "create_composio_client", lambda: fake
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def verify(self, proposal_id="approved-pr", **kwargs):
        response = self.client.post(VERIFY.format(proposal_id), headers=WRITE, **kwargs)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def statuses(self, body):
        return {name: item["status"] for name, item in body["checks"].items()}

    def test_requires_review_token(self):
        response = self.client.post(VERIFY.format("approved-pr"))
        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.composio.calls, [])

    def test_all_verified_without_activation_or_secrets(self):
        body = self.verify()
        self.assertEqual(set(self.statuses(body).values()), {"verified"})
        self.assertTrue(body["connections_verified"])
        self.assertFalse(body["execution_triggered"])
        self.assertEqual(body["operational_status"], "setup_required")
        self.assertEqual(body["github_source"]["mode"], "simulated")

        text = str(body)
        for secret in ["test-composio-key", "ca_test_slack", "user-test", "xoxb", "'eng'",
                       "test-langflow-key", FLOW_ID, "sk-flow-secret"]:
            self.assertNotIn(secret, text)

        self.assertEqual([name for name, _ in self.composio.calls],
                         ["connected_accounts.get", "tools.proxy"])
        proxy = self.composio.calls[1][1]
        self.assertEqual(proxy["method"], "GET")
        self.assertTrue(proxy["endpoint"].endswith("/conversations.info"))
        self.assertEqual(proxy["connected_account_id"], "ca_test_slack")
        self.assertEqual(proxy["parameters"][0]["value"], "C0123456789")

        self.assertEqual(self.fake.configs["approved-pr"], SAVED)
        self.assertEqual(self.fake.upserts, [])

    def test_not_configured_is_blocked(self):
        with mock.patch.dict(os.environ, {"COMPOSIO_SLACK_CONNECTED_ACCOUNT_ID": ""}):
            body = self.verify()
        self.assertEqual(body["checks"]["slack_account"]["status"], "blocked")
        self.assertEqual(body["checks"]["slack_channel"]["status"], "blocked")
        self.assertFalse(body["connections_verified"])
        self.assertEqual(self.composio.calls, [])

    def test_account_failures(self):
        cases = {
            "expired": account(status="EXPIRED"),
            "disabled": account(is_disabled=True),
            "wrong user": account(user_id="someone-else"),
            "wrong toolkit": account(toolkit="github"),
            "not in project": not_found(),
        }
        for name, result in cases.items():
            with self.subTest(name):
                self.use_composio(FakeComposio(account_result=result))
                body = self.verify()
                self.assertEqual(body["checks"]["slack_account"]["status"], "failed")
                self.assertEqual(body["checks"]["slack_channel"]["status"], "blocked")
                self.assertFalse(body["connections_verified"])
                self.assertEqual(len(self.composio.calls), 1)

    def test_channel_results(self):
        cases = {
            "not found": ({"ok": False, "error": "channel_not_found"}, "failed"),
            "archived": ({"ok": True, "channel": {"is_archived": True, "is_member": True}}, "failed"),
            "missing scope": ({"ok": False, "error": "missing_scope"}, "unverified"),
            "unknown error": ({"ok": False, "error": "ratelimited"}, "unverified"),
            "not member": ({"ok": True, "channel": {"is_member": False}}, "unverified"),
            "no membership info": ({"ok": True, "channel": {}}, "unverified"),
            "unexpected shape": ({"unexpected": True}, "unverified"),
        }
        for name, (data, expected) in cases.items():
            with self.subTest(name):
                self.use_composio(FakeComposio(channel_data=data))
                body = self.verify()
                self.assertEqual(body["checks"]["slack_account"]["status"], "verified")
                self.assertEqual(body["checks"]["slack_channel"]["status"], expected)
                self.assertFalse(body["connections_verified"])

    def test_unexpected_sdk_error_is_unverified(self):
        self.use_composio(FakeComposio(proxy_error=RuntimeError("boom")))
        body = self.verify()
        self.assertEqual(body["checks"]["slack_account"]["status"], "unverified")
        self.assertFalse(body["connections_verified"])

    def test_unapproved_candidate_skips_slack(self):
        body = self.verify("pending-pr")
        self.assertEqual(body["checks"]["candidate"]["status"], "failed")
        self.assertEqual(body["checks"]["slack_account"]["status"], "blocked")
        self.assertEqual(self.composio.calls, [])

    def test_missing_setup_skips_slack(self):
        body = self.verify("unsaved-pr")
        self.assertEqual(body["checks"]["configuration"]["status"], "failed")
        self.assertEqual(body["checks"]["slack_account"]["status"], "blocked")
        self.assertIsNone(body["operational_status"])
        self.assertEqual(self.composio.calls, [])

    def test_unknown_candidate(self):
        response = self.client.post(VERIFY.format("missing"), headers=WRITE)
        self.assertEqual(response.status_code, 404)

    def test_request_body_cannot_spoof_result(self):
        self.use_composio(FakeComposio(account_result=account(status="EXPIRED")))
        body = self.verify(json={
            "connections_verified": True,
            "checks": {"slack_account": {"status": "verified"}},
            "slack_channel_id": "C9999999999",
        })
        self.assertEqual(body["checks"]["slack_account"]["status"], "failed")
        self.assertFalse(body["connections_verified"])


if __name__ == "__main__":
    unittest.main()
