import logging
import os
import secrets
from datetime import datetime, timezone
from typing import Literal

import httpx
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, ConfigDict, field_validator

import automation_contract


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/candidates", tags=["Automation Setup"])

TEMPLATE_TYPE = automation_contract.TEMPLATE_TYPE
CONFIG_SELECT = (
    "proposal_id,template_type,allowed_repository,"
    "slack_channel_id,operational_status,event_source_mode,"
    "activated_at,disabled_at,created_at,updated_at"
)
ACTIVATION_SELECT = (
    f"{CONFIG_SELECT},activated_slack_account_id,activated_execution_flow_id"
)
PUBLIC_FIELDS = CONFIG_SELECT.split(",")
RESET_ACTIVATION = {
    "operational_status": "setup_required",
    "activated_at": None,
    "activated_slack_account_id": None,
    "activated_execution_flow_id": None,
    "disabled_at": None,
}

SUPPORTED_TRIGGER = automation_contract.SUPPORTED_TRIGGER
REPOSITORY_PATTERN = automation_contract.REPOSITORY_PATTERN
CHANNEL_ID_PATTERN = automation_contract.CHANNEL_ID_PATTERN


class AutomationConfigInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    template_type: Literal["github_pr_merged_to_slack"] = TEMPLATE_TYPE
    allowed_repository: str
    slack_channel_id: str

    @field_validator("allowed_repository")
    @classmethod
    def check_repository(cls, value: str) -> str:
        value = value.strip()
        if not REPOSITORY_PATTERN.fullmatch(value):
            raise ValueError("Repository must be in owner/name format")
        return value

    @field_validator("slack_channel_id")
    @classmethod
    def check_channel_id(cls, value: str) -> str:
        value = value.strip()
        if not CHANNEL_ID_PATTERN.fullmatch(value):
            raise ValueError(
                "Slack Channel ID must start with C or G, e.g. C0123456789"
            )
        return value


def is_supported_automation(proposal: dict, title: str = "") -> bool:
    """Only proposals that explicitly name Slack (or a reviewed destination
    resolution) qualify. Generic "notify a downstream tool" proposals do not."""
    return automation_contract.is_supported(proposal, title)


def require_token(value: str | None, env_name: str) -> None:
    expected_token = os.getenv(env_name)

    if not expected_token:
        raise HTTPException(
            status_code=500,
            detail="Automation setup API is not configured",
        )

    if not value or not secrets.compare_digest(value, expected_token):
        raise HTTPException(status_code=401, detail="Unauthorized")


def supabase_settings() -> tuple[str, dict]:
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_SECRET_KEY")

    if not supabase_url or not supabase_key:
        raise HTTPException(
            status_code=500,
            detail="Supabase configuration is missing",
        )

    return supabase_url.rstrip("/"), {"apikey": supabase_key}


def raise_for_config_table(response: httpx.Response) -> None:
    if response.status_code in {400, 404}:
        try:
            body = response.json()
            code = body.get("code") if isinstance(body, dict) else None
        except ValueError:
            code = None
        if code in {"PGRST205", "42P01"}:
            raise HTTPException(
                status_code=503,
                detail=(
                    "Automation config table is missing. Apply "
                    "backend/migrations/001_automation_configs.sql."
                ),
            )
        if code in {"PGRST204", "42703", "23514"}:
            raise HTTPException(
                status_code=503,
                detail=(
                    "Automation config table is out of date. Apply "
                    "backend/migrations/002_automation_activation.sql."
                ),
            )
    response.raise_for_status()


def public_config(row: dict | None) -> dict | None:
    if row is None:
        return None
    return {key: row.get(key) for key in PUBLIC_FIELDS}


def current_activation_context() -> dict:
    """Server-side values an activation depends on. Changing either one
    invalidates an existing activation."""
    return {
        "activated_slack_account_id": os.getenv("COMPOSIO_SLACK_CONNECTED_ACCOUNT_ID") or None,
        "activated_execution_flow_id": os.getenv("LANGFLOW_EXECUTION_FLOW_ID") or None,
    }


def config_url(base_url: str) -> str:
    return f"{base_url}/rest/v1/automation_configs"


async def fetch_config(
    client: httpx.AsyncClient, base_url: str, headers: dict, proposal_id: str
) -> dict | None:
    response = await client.get(
        config_url(base_url),
        headers=headers,
        params={"proposal_id": f"eq.{proposal_id}", "select": ACTIVATION_SELECT},
    )
    raise_for_config_table(response)
    rows = response.json()

    if not isinstance(rows, list):
        raise ValueError("Unexpected Supabase response")

    return rows[0] if rows else None


async def patch_config(
    client: httpx.AsyncClient,
    base_url: str,
    headers: dict,
    filters: dict,
    changes: dict,
) -> list:
    """PATCH rows matching every eq-filter; returns the updated rows."""
    response = await client.patch(
        config_url(base_url),
        headers={
            **headers,
            "Content-Type": "application/json",
            "Prefer": "return=representation",
        },
        params={
            **{key: f"eq.{value}" for key, value in filters.items()},
            "select": ACTIVATION_SELECT,
        },
        json={**changes, "updated_at": datetime.now(timezone.utc).isoformat()},
    )
    raise_for_config_table(response)
    rows = response.json()

    if not isinstance(rows, list):
        raise ValueError("Unexpected Supabase response")

    return rows


async def invalidate_stale_activation(
    client: httpx.AsyncClient, base_url: str, headers: dict, row: dict | None
) -> dict | None:
    """Drop an activation whose Slack account or Execution Flow ID changed
    on the server since it was verified."""
    if not row or row.get("operational_status") != "active":
        return row

    context = current_activation_context()
    if all(row.get(key) == value for key, value in context.items()):
        return row

    rows = await patch_config(
        client,
        base_url,
        headers,
        {"proposal_id": row["proposal_id"], "operational_status": "active"},
        RESET_ACTIVATION,
    )
    if rows:
        return rows[0]
    return await fetch_config(client, base_url, headers, row["proposal_id"])


async def fetch_candidate(
    client: httpx.AsyncClient, base_url: str, headers: dict, proposal_id: str
) -> dict:
    response = await client.get(
        f"{base_url}/rest/v1/automation_candidates",
        headers=headers,
        params={
            "proposal_id": f"eq.{proposal_id}",
            "select": "proposal_id,title,review_status,validation_result,proposal",
        },
    )
    response.raise_for_status()
    candidates = response.json()

    if not isinstance(candidates, list):
        raise ValueError("Unexpected Supabase response")

    if not candidates:
        raise HTTPException(status_code=404, detail="Candidate not found")

    return candidates[0]


def eligibility_error(candidate: dict, config: dict | None = None) -> tuple[int, str] | None:
    """Return (status_code, detail) when the candidate cannot be configured.

    With config, a resolved proposal's setup must also use exactly the
    repository and Slack channel that were approved with it."""
    proposal = candidate.get("proposal")
    proposal = proposal if isinstance(proposal, dict) else {}

    if candidate.get("review_status") != "approved":
        return 409, "Only approved candidates can be configured"

    if (
        candidate.get("validation_result") or proposal.get("validation_result")
    ) == "blocked":
        return 409, "Candidate is blocked by SOP validation"

    problems = automation_contract.contract_problems(
        proposal, candidate.get("title") or "", candidate.get("proposal_id")
    )
    if problems:
        return 422, (
            "Candidate is not an explicit GitHub pull request merged "
            f"to Slack automation: {problems[0]}"
        )

    if automation_contract.destination_mismatch(proposal, config):
        return 422, (
            "Setup must use the repository and Slack channel approved "
            "with this resolved proposal"
        )

    return None


@router.get("/{proposal_id}/automation-config")
async def get_automation_config(
    proposal_id: str,
    x_ingest_token: str | None = Header(default=None),
):
    require_token(x_ingest_token, "CANDIDATE_INGEST_TOKEN")
    base_url, headers = supabase_settings()

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            row = await fetch_config(client, base_url, headers, proposal_id)
            row = await invalidate_stale_activation(client, base_url, headers, row)

    except (httpx.HTTPError, ValueError):
        logger.exception("Failed to fetch automation config")
        raise HTTPException(
            status_code=502,
            detail="Failed to fetch automation config",
        )

    return {"data": public_config(row)}


@router.put("/{proposal_id}/automation-config")
async def save_automation_config(
    proposal_id: str,
    payload: AutomationConfigInput,
    x_review_token: str | None = Header(default=None),
):
    require_token(x_review_token, "CANDIDATE_REVIEW_TOKEN")
    base_url, headers = supabase_settings()

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            candidate = await fetch_candidate(
                client, base_url, headers, proposal_id
            )
            error = eligibility_error(candidate, payload.model_dump())
            if error:
                raise HTTPException(status_code=error[0], detail=error[1])

            response = await client.post(
                config_url(base_url),
                headers={
                    **headers,
                    "Content-Type": "application/json",
                    "Prefer": "resolution=merge-duplicates,return=representation",
                },
                params={"on_conflict": "proposal_id", "select": CONFIG_SELECT},
                json={
                    "proposal_id": proposal_id,
                    "template_type": payload.template_type,
                    "allowed_repository": payload.allowed_repository,
                    "slack_channel_id": payload.slack_channel_id,
                    **RESET_ACTIVATION,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
            )

        raise_for_config_table(response)
        rows = response.json()

        if not isinstance(rows, list) or not rows:
            raise ValueError("Unexpected Supabase response")

    except (httpx.HTTPError, ValueError):
        logger.exception("Failed to save automation config")
        raise HTTPException(
            status_code=502,
            detail="Failed to save automation config",
        )

    return {
        "message": "Automation config saved",
        "data": rows[0],
        "execution_triggered": False,
    }
