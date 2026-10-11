import logging
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Header, HTTPException
from pydantic import ValidationError

import automation_contract
from automation_config_api import (
    TEMPLATE_TYPE,
    AutomationConfigInput,
    fetch_config,
    require_token,
    supabase_settings,
)
from candidates_api import CandidateProposal, save_candidate_to_supabase


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/candidates", tags=["Automation Setup"])

CANDIDATE_SELECT = (
    "proposal_id,title,proposal,evidence_event_ids,"
    "validation_result,sop_status,review_status"
)


async def fetch_original(client, base_url, headers, proposal_id) -> dict:
    response = await client.get(
        f"{base_url}/rest/v1/automation_candidates",
        headers=headers,
        params={"proposal_id": f"eq.{proposal_id}", "select": CANDIDATE_SELECT},
    )
    response.raise_for_status()
    rows = response.json()
    if not isinstance(rows, list):
        raise ValueError("Unexpected Supabase response")
    if not rows:
        raise HTTPException(status_code=404, detail="Candidate not found")
    return rows[0]


def saved_destination(config: dict | None) -> dict:
    """The reviewer-saved setup is the only accepted destination source."""
    if config is None:
        raise HTTPException(
            status_code=409,
            detail="Save the automation setup (repository and Slack channel) for this candidate first",
        )
    if config.get("template_type") != TEMPLATE_TYPE:
        raise HTTPException(status_code=409, detail="Saved setup uses an unsupported template")
    try:
        setup = AutomationConfigInput(
            template_type=TEMPLATE_TYPE,
            allowed_repository=config.get("allowed_repository") or "",
            slack_channel_id=config.get("slack_channel_id") or "",
        )
    except ValidationError:
        raise HTTPException(
            status_code=409,
            detail="Saved setup has an invalid repository or Slack Channel ID",
        )
    return {
        "allowed_repository": setup.allowed_repository,
        "slack_channel_id": setup.slack_channel_id,
    }


@router.post("/{proposal_id}/resolve-destination", status_code=201)
async def resolve_destination(
    proposal_id: str,
    x_review_token: str | None = Header(default=None),
):
    require_token(x_review_token, "CANDIDATE_REVIEW_TOKEN")
    base_url, headers = supabase_settings()

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            original = await fetch_original(client, base_url, headers, proposal_id)
            config = await fetch_config(client, base_url, headers, proposal_id)
    except (httpx.HTTPError, ValueError):
        logger.exception("Failed to load candidate for destination resolution")
        raise HTTPException(status_code=502, detail="Failed to load candidate")

    blockers = automation_contract.resolution_blockers(original)
    if blockers:
        raise HTTPException(status_code=409, detail=f"Cannot resolve this candidate: {blockers[0]}")

    destination = saved_destination(config)
    resolved = automation_contract.build_resolved_proposal(
        original, destination, datetime.now(timezone.utc).isoformat()
    )

    problems = automation_contract.contract_problems(
        resolved, resolved["title"], resolved["proposal_id"]
    )
    if problems:
        logger.error("Resolved proposal failed the contract: %s", problems)
        raise HTTPException(status_code=500, detail="Resolved proposal failed validation")

    try:
        proposal = CandidateProposal(**resolved)
    except ValidationError:
        logger.exception("Resolved proposal failed candidate validation")
        raise HTTPException(status_code=500, detail="Resolved proposal failed validation")

    created = await save_candidate_to_supabase(proposal)
    if not created:
        raise HTTPException(
            status_code=409,
            detail=f"Resolved proposal {proposal.proposal_id} already exists",
        )

    return {
        "message": "Resolved proposal created. It needs its own review before setup.",
        "proposal_id": proposal.proposal_id,
        "resolved_from": proposal_id,
        "review_status": "pending_review",
        "validation_result": proposal.validation_result,
        "sop_status": proposal.sop_status,
        "execution_triggered": False,
    }
