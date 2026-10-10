
import os
import secrets
import logging
from typing import Literal

import httpx
from dotenv import load_dotenv
from fastapi import APIRouter, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from fastapi import Query

from datetime import datetime, timezone
from typing import Literal

load_dotenv()

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/candidates", tags=["Automation Candidates"])


class CandidateProposal(BaseModel):
    # Preserve all additional fields from SOP Validator.
    model_config = ConfigDict(extra="allow")

    proposal_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    evidence_event_ids: list[str]
    validation_result: Literal["pass", "conditional", "blocked"]
    sop_status: str = Field(min_length=1)
    auto_execute_allowed: bool


async def save_candidate_to_supabase(proposal: CandidateProposal,) -> bool:
    """
    Insert candidate into Supabase.

    Returns:
        True  -> candidate inserted
        False -> proposal_id already exists

    Never overwrites an existing review decision.
    """

    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_SECRET_KEY")

    if not supabase_url or not supabase_key:
        raise HTTPException(
            status_code=500,
            detail="Supabase server configuration is missing",
        )

    row = {
        "proposal_id": proposal.proposal_id,
        "title": proposal.title,
        "proposal": proposal.model_dump(mode="json"),
        "evidence_event_ids": proposal.evidence_event_ids,
        "validation_result": proposal.validation_result,
        "sop_status": proposal.sop_status,
        "review_status": "pending_review",
    }

    headers = {
        "apikey": supabase_key,
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                f"{supabase_url.rstrip('/')}"
                "/rest/v1/automation_candidates",
                headers=headers,
                json=row,
            )

        if response.status_code == 409:
            return False

        response.raise_for_status()

    except httpx.HTTPError as exc:
        logger.exception("Failed to save automation candidate")
        raise HTTPException(
            status_code=502,
            detail="Failed to save candidate to Supabase",
        ) from exc

    return True

@router.post("", status_code=status.HTTP_201_CREATED)
async def create_candidate(
    proposal: CandidateProposal,
    x_ingest_token: str | None = Header(default=None),
):
    expected_token = os.getenv("CANDIDATE_INGEST_TOKEN")

    if not expected_token:
        raise HTTPException(
            status_code=500,
            detail="Candidate ingestion is not configured",
        )

    if not x_ingest_token or not secrets.compare_digest(
        x_ingest_token, expected_token
    ):
        raise HTTPException(
            status_code=401,
            detail="Unauthorized",
        )

    created = await save_candidate_to_supabase(proposal)

    if not created:
        raise HTTPException(
            status_code=409,
            detail="Proposal ID already exists",
        )

    return {
        "message": "Automation candidate saved",
        "proposal_id": proposal.proposal_id,
        "review_status": "pending_review",
        "validation_result": proposal.validation_result,
    }


@router.get("")
async def get_candidates(
    limit: int = Query(default=20, ge=1, le=100),
    review_status: Literal[
        "pending_review", "approved", "rejected"
    ] | None = Query(default=None),
    x_ingest_token: str | None = Header(default=None),
):
    # Temporary protection for backend testing.
    # Never expose this token in React.
    expected_token = os.getenv("CANDIDATE_INGEST_TOKEN")

    if not expected_token:
        raise HTTPException(
            status_code=500,
            detail="Candidate API is not configured",
        )

    if not x_ingest_token or not secrets.compare_digest(
        x_ingest_token, expected_token
    ):
        raise HTTPException(
            status_code=401,
            detail="Unauthorized",
        )

    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_SECRET_KEY")

    if not supabase_url or not supabase_key:
        raise HTTPException(
            status_code=500,
            detail="Supabase configuration is missing",
        )

    headers = {
        "apikey": supabase_key,
    }

    params = {
        "select": (
            "proposal_id,title,proposal,"
            "evidence_event_ids,validation_result,"
            "sop_status,review_status,created_at,updated_at"
        ),
        "order": "created_at.desc",
        "limit": limit,
    }

    # Optional filter; omitting it keeps the original "all candidates" result.
    if review_status:
        params["review_status"] = f"eq.{review_status}"

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                f"{supabase_url.rstrip('/')}/rest/v1/automation_candidates",
                headers=headers,
                params=params,
            )

        response.raise_for_status()
        candidates = response.json()

        if not isinstance(candidates, list):
            raise ValueError("Unexpected response from Supabase")

    except (httpx.HTTPError, ValueError):
        logger.exception("Failed to fetch automation candidates")
        raise HTTPException(
            status_code=502,
            detail="Failed to fetch automation candidates",
        )

    return {
        "total": len(candidates),
        "data": candidates,
    }


class CandidateDecision(BaseModel):
    decision: Literal["approved", "rejected"]


@router.patch("/{proposal_id}/decision")
async def update_candidate_decision(
    proposal_id: str,
    payload: CandidateDecision,
    x_review_token: str | None = Header(default=None),
):
    expected_token = os.getenv("CANDIDATE_REVIEW_TOKEN")

    if not expected_token:
        raise HTTPException(
            status_code=500,
            detail="Review API is not configured",
        )

    if not x_review_token or not secrets.compare_digest(
        x_review_token, expected_token
    ):
        raise HTTPException(
            status_code=401,
            detail="Unauthorized",
        )

    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_SECRET_KEY")

    if not supabase_url or not supabase_key:
        raise HTTPException(
            status_code=500,
            detail="Supabase configuration is missing",
        )

    headers = {
        "apikey": supabase_key,
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }

    # Update only candidates awaiting a decision.
    params = {
        "proposal_id": f"eq.{proposal_id}",
        "review_status": "eq.pending_review",
        "select": (
            "proposal_id,review_status,"
            "validation_result,sop_status,updated_at"
        ),
    }

    update_data = {
        "review_status": payload.decision,
        "updated_at": datetime.now(
            timezone.utc
        ).isoformat(),
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.patch(
                f"{supabase_url.rstrip('/')}/rest/v1/automation_candidates",
                headers=headers,
                params=params,
                json=update_data,
            )

        response.raise_for_status()
        updated_rows = response.json()

        if not isinstance(updated_rows, list):
            raise ValueError("Unexpected Supabase response")

    except (httpx.HTTPError, ValueError):
        logger.exception("Failed to update candidate decision")
        raise HTTPException(
            status_code=502,
            detail="Failed to update candidate decision",
        )

    if not updated_rows:
        raise HTTPException(
            status_code=409,
            detail="Candidate not found or already reviewed",
        )

    candidate = updated_rows[0]

    return {
        "message": "Review decision saved",
        "proposal_id": candidate["proposal_id"],
        "review_status": candidate["review_status"],
        "validation_result": candidate["validation_result"],
        "sop_status": candidate["sop_status"],
        "execution_triggered": False,
    }
