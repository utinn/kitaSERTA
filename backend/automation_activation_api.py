import logging
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Header, HTTPException

from automation_config_api import (
    RESET_ACTIVATION,
    fetch_config,
    patch_config,
    public_config,
    require_token,
    supabase_settings,
)
from connection_verification_api import (
    all_verified,
    github_source,
    run_readiness_checks,
)


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/candidates", tags=["Automation Setup"])

EVENT_SOURCE_MODE = "simulated"


def supabase_error(action: str) -> HTTPException:
    logger.exception("Failed to %s automation", action)
    return HTTPException(status_code=502, detail=f"Failed to {action} automation")


@router.post("/{proposal_id}/automation-config/activate")
async def activate_automation(
    proposal_id: str,
    x_review_token: str | None = Header(default=None),
):
    require_token(x_review_token, "CANDIDATE_REVIEW_TOKEN")
    result = await run_readiness_checks(proposal_id)
    checks, config, context = result["checks"], result["config"], result["context"]
    base_url, headers = supabase_settings()

    response = {
        "proposal_id": proposal_id,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "checks": checks,
        "github_source": github_source(),
        "event_source_mode": EVENT_SOURCE_MODE,
        "execution_triggered": False,
    }

    if config is None or not all_verified(checks):
        if config is not None and config.get("operational_status") == "active":
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    rows = await patch_config(
                        client,
                        base_url,
                        headers,
                        {"proposal_id": proposal_id, "operational_status": "active"},
                        RESET_ACTIVATION,
                    )
                    config = rows[0] if rows else await fetch_config(
                        client, base_url, headers, proposal_id
                    )
            except (httpx.HTTPError, ValueError):
                raise supabase_error("deactivate")

        return {
            **response,
            "activated": False,
            "message": "Not activated: every check must be verified.",
            "data": public_config(config),
        }

    changes = {
        "operational_status": "active",
        "event_source_mode": EVENT_SOURCE_MODE,
        "disabled_at": None,
        **context,
    }
    if config.get("operational_status") != "active":
        changes["activated_at"] = datetime.now(timezone.utc).isoformat()

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            rows = await patch_config(
                client,
                base_url,
                headers,
                {
                    "proposal_id": proposal_id,
                    "template_type": config["template_type"],
                    "allowed_repository": config["allowed_repository"],
                    "slack_channel_id": config["slack_channel_id"],
                    "operational_status": config["operational_status"],
                },
                changes,
            )
    except (httpx.HTTPError, ValueError):
        raise supabase_error("activate")

    if not rows:
        raise HTTPException(
            status_code=409,
            detail="Setup changed during activation. Verify and activate again.",
        )

    return {
        **response,
        "activated": True,
        "message": "Automation active for simulated events. Nothing was executed.",
        "data": public_config(rows[0]),
    }


@router.post("/{proposal_id}/automation-config/disable")
async def disable_automation(
    proposal_id: str,
    x_review_token: str | None = Header(default=None),
):
    require_token(x_review_token, "CANDIDATE_REVIEW_TOKEN")
    base_url, headers = supabase_settings()

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            rows = await patch_config(
                client,
                base_url,
                headers,
                {"proposal_id": proposal_id, "operational_status": "active"},
                {
                    "operational_status": "disabled",
                    "disabled_at": datetime.now(timezone.utc).isoformat(),
                    "activated_slack_account_id": None,
                    "activated_execution_flow_id": None,
                },
            )
            config = rows[0] if rows else await fetch_config(
                client, base_url, headers, proposal_id
            )
    except (httpx.HTTPError, ValueError):
        raise supabase_error("disable")

    if config is None:
        raise HTTPException(status_code=404, detail="No automation setup saved")

    if config.get("operational_status") == "setup_required":
        raise HTTPException(status_code=409, detail="Automation is not active")

    return {
        "message": "Automation disabled",
        "data": public_config(config),
        "execution_triggered": False,
    }
