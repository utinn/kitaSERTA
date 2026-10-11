import json
import logging
import os
import uuid

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, ValidationError

from candidates_api import (CandidateProposal, save_candidate_to_supabase)


router = APIRouter(prefix="/api/analysis", tags=["Analysis"])
logger = logging.getLogger(__name__)

LANGFLOW_URL = os.getenv(
    "LANGFLOW_URL", "http://127.0.0.1:7860"
).rstrip("/")

FLOW_ID = os.getenv("LANGFLOW_ANALYSIS_FLOW_ID")
FILTER_ID = os.getenv("LANGFLOW_FREQUENCY_FILTER_ID")
API_KEY = os.getenv("LANGFLOW_API_KEY")

DETECTION_GROUP_BY = ["tool", "action"]

class AnalysisSettings(BaseModel):
    min_occurrences: int = Field(default=3, ge=1, le=100)
    window_days: int = Field(default=7, ge=1, le=90)

async def execute_langflow(settings: AnalysisSettings) -> dict:
    if not all([FLOW_ID, FILTER_ID, API_KEY]):
        raise HTTPException(
            status_code=500,
            detail="Langflow configuration is incomplete"
        )

    payload = {
        "input_type": "text",
        "output_type": "chat",
        "input_value": "Analyze activity patterns",
        "session_id": str(uuid.uuid4()),
        "tweaks": {
            FILTER_ID: {
                "min_occurrences": settings.min_occurrences,
                "window_days": settings.window_days,
            }
        },
    }

    try:
        async with httpx.AsyncClient(timeout=180.0) as client:
            response = await client.post(
                f"{LANGFLOW_URL}/api/v1/run/{FLOW_ID}",
                headers={"x-api-key": API_KEY},
                json=payload,
            )
            response.raise_for_status()
            return response.json()

    except httpx.TimeoutException as exc:
        logger.error("Langflow analysis timed out")
        raise HTTPException(
            status_code=504,
            detail="Langflow analysis timed out"
        ) from exc

    except httpx.HTTPStatusError as exc:
        logger.error(
            "Langflow HTTP %s: %.1000s",
            exc.response.status_code,
            exc.response.text,
        )
        raise HTTPException(
            status_code=502,
            detail=(
                f"Langflow returned HTTP "
                f"{exc.response.status_code}"
            ),
        ) from exc

    except httpx.RequestError as exc:
        logger.error("Langflow connection failed: %s", exc)
        raise HTTPException(
            status_code=502,
            detail="Cannot connect to Langflow"
        ) from exc

    except ValueError as exc:
        raise HTTPException(
            status_code=502,
            detail="Langflow returned invalid JSON"
        ) from exc
    
def extract_candidates(result: dict) -> list[dict]:
    try:
        message = (
            result["outputs"][0]["outputs"][0]
            ["results"]["message"]["text"]
        )
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError(
            "Unexpected Langflow response structure"
        ) from exc

    if not isinstance(message, str):
        raise ValueError("Langflow output must be text")

    text = message.strip()

    if text.startswith("```"):
        lines = text.splitlines()

        if len(lines) < 3 or lines[-1].strip() != "```":
            raise ValueError("Invalid Markdown JSON wrapper")

        text = "\n".join(lines[1:-1]).strip()

    try:
        proposal = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(
            "SOP Validator returned invalid JSON"
        ) from exc

    if not isinstance(proposal, dict):
        raise ValueError("Expected a proposal JSON object")

    proposal_id = proposal.get("proposal_id")
    trigger = proposal.get("trigger")
    evidence_ids = proposal.get("evidence_event_ids")
    steps = proposal.get("steps")

    if (
        isinstance(trigger, str)
        and trigger.strip().lower() in {"none", "null", ""}
        and evidence_ids == []
        and steps == []
    ):
        return []

    if proposal_id == "none" and evidence_ids == [] and steps == []:
        return []

    if not isinstance(proposal_id, str) or not proposal_id.strip():
        raise ValueError("Missing proposal_id")

    if not isinstance(proposal.get("evidence_event_ids"), list):
        raise ValueError("Invalid evidence_event_ids")

    if proposal.get("validation_result") not in (
        "pass", "conditional", "blocked"
    ):
        raise ValueError("Invalid validation_result")

    return [proposal]

@router.post("/run")
async def run_analysis(settings: AnalysisSettings):
    result = await execute_langflow(settings)

    
    nested = result["outputs"][0].get("outputs", [])

    logger.warning(
        "Nested output count: %s",
        len(nested) if isinstance(nested, list) else "not a list",
    )

    if isinstance(nested, list):
        for i, output in enumerate(nested):
            if not isinstance(output, dict):
                logger.warning("Output %s type: %s", i, type(output).__name__)
                continue

            results = output.get("results")

            logger.warning(
                "Output %s: keys=%s, results_type=%s, results_keys=%s",
                i,
                list(output.keys()),
                type(results).__name__,
                list(results.keys()) if isinstance(results, dict) else None,
            )

            if isinstance(results, dict):
                message = results.get("message")

                logger.warning(
                    "Output %s message_type=%s, message_keys=%s",
                    i,
                    type(message).__name__,
                    list(message.keys()) if isinstance(message, dict) else None,
                )

    try:
        candidates = extract_candidates(result)
    except ValueError as exc:
        logger.error("Analysis response parsing failed: %s", exc)
        raise HTTPException(
            status_code=502,
            detail=str(exc)
        ) from exc

    detection_settings = {
        **settings.model_dump(),
        "group_by": DETECTION_GROUP_BY,
    }
    candidates = [
        {**candidate, "detection_settings": detection_settings}
        for candidate in candidates
    ]

    try:
        validated_candidates = [
            CandidateProposal.model_validate(candidate)
            for candidate in candidates
        ]
    except ValidationError as exc:
        logger.error(
            "Invalid automation candidate: %s", exc
        )
        raise HTTPException(
            status_code=502,
            detail="Langflow returned an invalid candidate",
        ) from exc

    saved_ids = []
    duplicate_ids = []

    for candidate in validated_candidates:
        if (
            not candidate.evidence_event_ids
            or not candidate.model_extra
            or str(candidate.model_extra.get("trigger", "")).lower()
            in {"", "none", "null"}
        ):
            raise HTTPException(
                status_code=502,
                detail="Candidate lacks valid automation evidence",
            )

        created = await save_candidate_to_supabase(candidate)

        if created:
            saved_ids.append(candidate.proposal_id)
        else:
            duplicate_ids.append(candidate.proposal_id)

    return {
        "status": (
            "candidates_found" if candidates
            else "no_candidates"
        ),
        "settings": settings.model_dump(),
        "candidates": candidates,
        "persistence": {
            "saved": saved_ids,
            "already_exists": duplicate_ids,
        },
    }

