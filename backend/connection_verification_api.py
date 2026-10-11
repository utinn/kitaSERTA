import logging
import os
import uuid
from datetime import datetime, timezone

import httpx
from composio import Composio
from composio_client import APIConnectionError, APIStatusError, NotFoundError
from fastapi import APIRouter, Header, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import ValidationError

from automation_config_api import (
    TEMPLATE_TYPE,
    AutomationConfigInput,
    current_activation_context,
    eligibility_error,
    fetch_candidate,
    fetch_config,
    invalidate_stale_activation,
    public_config,
    require_token,
    supabase_settings,
)


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/candidates", tags=["Automation Setup"])

SLACK_CONVERSATION_INFO_URL = "https://slack.com/api/conversations.info"

SLACK_CHANNEL_FAILURES = {
    "channel_not_found": "Channel not found or not visible to the connected Slack account.",
    "invalid_auth": "Slack rejected the connected account's credentials.",
    "not_authed": "Slack rejected the connected account's credentials.",
    "token_revoked": "The connected Slack account's access was revoked.",
    "token_expired": "The connected Slack account's access has expired.",
    "account_inactive": "The connected Slack account is inactive.",
}
SKIPPED = {"status": "blocked", "detail": "Skipped until the checks above pass."}


def check(status: str, detail: str) -> dict:
    return {"status": status, "detail": detail}


def create_composio_client() -> Composio:
    return Composio(api_key=os.environ["COMPOSIO_API_KEY"])


def verify_slack_account(composio: Composio, account_id: str, user_id: str) -> dict:
    try:
        account = composio.connected_accounts.get(account_id)
    except NotFoundError:
        return check("failed", "Slack connected account not found in this Composio project.")
    except (APIConnectionError, APIStatusError):
        logger.exception("Composio connected account lookup failed")
        return check("unverified", "Could not reach Composio to check the Slack account.")

    toolkit = getattr(getattr(account, "toolkit", None), "slug", None)
    if str(toolkit).lower() != "slack":
        return check("failed", "Configured connected account is not a Slack account.")

    if account.user_id != user_id:
        return check("failed", "Slack account belongs to a different Composio user.")

    if account.is_disabled:
        return check("failed", "Slack connected account is disabled in Composio.")

    if account.status != "ACTIVE":
        return check(
            "failed",
            f"Slack connected account status is {account.status.lower()}, not active.",
        )

    return check("verified", "Active Slack account connected for the configured Composio user.")


def verify_slack_channel(composio: Composio, account_id: str, channel_id: str) -> dict:
    try:
        response = composio.tools.proxy(
            endpoint=SLACK_CONVERSATION_INFO_URL,
            method="GET",
            connected_account_id=account_id,
            parameters=[{"name": "channel", "type": "query", "value": channel_id}],
        )
    except (APIConnectionError, APIStatusError):
        logger.exception("Slack channel lookup through Composio failed")
        return check("unverified", "Could not check the channel through Composio.")

    data = response.data if isinstance(response.data, dict) else {}

    if int(response.status) != 200 or "ok" not in data:
        return check("unverified", "Slack returned an unexpected response for the channel check.")

    if not data["ok"]:
        error = str(data.get("error", ""))
        if error in SLACK_CHANNEL_FAILURES:
            return check("failed", SLACK_CHANNEL_FAILURES[error])
        if error == "missing_scope":
            return check(
                "unverified",
                "The Slack connection lacks permission to read channel details.",
            )
        return check("unverified", "Slack could not confirm access to the channel.")

    channel = data.get("channel")
    if not isinstance(channel, dict):
        channel = {}

    if channel.get("is_archived"):
        return check("failed", "The Slack channel is archived.")

    if channel.get("is_member") is True:
        return check("verified", "Channel is accessible and the connected account is a member.")

    return check(
        "unverified",
        "Channel exists, but the connected account is not confirmed as a member. Posting may be refused.",
    )


def verify_slack(channel_id: str) -> tuple[dict, dict]:
    account_id = os.getenv("COMPOSIO_SLACK_CONNECTED_ACCOUNT_ID")
    user_id = os.getenv("COMPOSIO_USER_ID")

    if not os.getenv("COMPOSIO_API_KEY") or not account_id or not user_id:
        return (
            check("blocked", "Slack verification is not configured on the server."),
            SKIPPED,
        )

    composio = create_composio_client()
    account = verify_slack_account(composio, account_id, user_id)
    if account["status"] != "verified":
        return account, SKIPPED

    return account, verify_slack_channel(composio, account_id, channel_id)


def github_source() -> dict:
    return {
        "mode": "simulated",
        "detail": "Pull request events come from the demo activity form, not a live GitHub webhook.",
    }


async def verify_execution_flow() -> dict:
    """Confirm the configured Flow 3 exists in Langflow by reading its
    definition (GET /api/v1/flows/{id}). The flow is never run."""
    flow_id = os.getenv("LANGFLOW_EXECUTION_FLOW_ID")
    api_key = os.getenv("LANGFLOW_API_KEY")
    langflow_url = os.getenv("LANGFLOW_URL", "http://127.0.0.1:7860").rstrip("/")

    if not flow_id or not api_key:
        return check("blocked", "Execution Flow ID is not configured on the server.")

    try:
        uuid.UUID(flow_id)
    except ValueError:
        return check("failed", "Configured Execution Flow ID is not a valid flow ID.")

    if flow_id == os.getenv("LANGFLOW_ANALYSIS_FLOW_ID"):
        return check("failed", "Execution Flow ID points at the analysis flow (Flow 2).")

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                f"{langflow_url}/api/v1/flows/{flow_id}",
                headers={"x-api-key": api_key},
            )
    except httpx.HTTPError:
        logger.exception("Langflow execution flow lookup failed")
        return check("unverified", "Could not reach Langflow to check the Execution Flow.")

    if response.status_code == 404:
        return check("failed", "Execution Flow not found in Langflow.")
    if response.status_code in {401, 403}:
        return check("unverified", "Langflow refused the Execution Flow lookup.")

    try:
        body = response.json()
    except ValueError:
        body = None

    if response.status_code != 200 or not isinstance(body, dict) or str(body.get("id")) != flow_id:
        return check("unverified", "Langflow returned an unexpected response for the Execution Flow.")

    return check("verified", "Execution Flow exists in Langflow. It was not run.")


def config_check(config: dict | None) -> dict:
    if config is None:
        return check("failed", "No setup saved yet.")
    if config.get("template_type") != TEMPLATE_TYPE:
        return check("failed", "Saved setup uses an unsupported template.")
    try:
        AutomationConfigInput(
            template_type=TEMPLATE_TYPE,
            allowed_repository=config.get("allowed_repository") or "",
            slack_channel_id=config.get("slack_channel_id") or "",
        )
    except ValidationError:
        return check("failed", "Saved setup has an invalid repository or Slack Channel ID.")
    return check("verified", "Allowed repository and Slack Channel ID are valid.")


async def run_readiness_checks(proposal_id: str) -> dict:
    """Run every activation prerequisite from server-side state only.

    Returns {"checks", "config" (full row, server-only), "context"}.
    """
    base_url, headers = supabase_settings()
    context = current_activation_context()

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            candidate = await fetch_candidate(client, base_url, headers, proposal_id)
            config = await fetch_config(client, base_url, headers, proposal_id)
            config = await invalidate_stale_activation(client, base_url, headers, config)

    except (httpx.HTTPError, ValueError):
        logger.exception("Failed to load automation setup for verification")
        raise HTTPException(
            status_code=502,
            detail="Failed to load automation setup",
        )

    error = eligibility_error(candidate, config)
    candidate_result = (
        check("failed", error[1]) if error
        else check("verified", "Approved, not SOP-blocked, and uses the supported template.")
    )
    config_result = config_check(config)

    if config is not None and candidate_result["status"] == config_result["status"] == "verified":
        try:
            slack_account, slack_channel = await run_in_threadpool(
                verify_slack, config["slack_channel_id"]
            )
        except Exception:
            logger.exception("Slack verification failed unexpectedly")
            slack_account = check("unverified", "Slack verification could not be completed.")
            slack_channel = SKIPPED
        execution_flow = await verify_execution_flow()
    else:
        slack_account, slack_channel, execution_flow = SKIPPED, SKIPPED, SKIPPED

    return {
        "checks": {
            "candidate": candidate_result,
            "configuration": config_result,
            "slack_account": slack_account,
            "slack_channel": slack_channel,
            "execution_flow": execution_flow,
        },
        "config": config,
        "context": context,
    }


def all_verified(checks: dict) -> bool:
    return all(item["status"] == "verified" for item in checks.values())


@router.post("/{proposal_id}/automation-config/verify")
async def verify_automation_connections(
    proposal_id: str,
    x_review_token: str | None = Header(default=None),
):
    require_token(x_review_token, "CANDIDATE_REVIEW_TOKEN")
    result = await run_readiness_checks(proposal_id)
    config = public_config(result["config"])

    return {
        "proposal_id": proposal_id,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "checks": result["checks"],
        "github_source": github_source(),
        "connections_verified": all_verified(result["checks"]),
        "operational_status": config["operational_status"] if config else None,
        "data": config,
        "execution_triggered": False,
    }
