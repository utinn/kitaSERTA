import logging
import re
from datetime import datetime

import httpx
from fastapi import APIRouter, Header, HTTPException, Query

from automation_config_api import require_token, supabase_settings


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/candidates", tags=["Execution History"])

DEFAULT_LIMIT = 20
MAX_LIMIT = 50
HISTORY_SELECT = (
    "source_event_id,status,event_source_mode,allowed_repository,"
    "claimed_at,finished_at,error,evidence"
)
STATUS_SUMMARIES = {
    "claimed": (
        "In progress, or the backend stopped before recording a result. "
        "Not retried; review manually."
    ),
    "verified": (
        "A Slack message containing this event ID was found in the approved channel."
    ),
    "unverified": (
        "Flow 3 finished, but delivery could not be confirmed. "
        "Do not treat this as a successful send."
    ),
    "unknown": (
        "Ambiguous result: a message may or may not have been sent. "
        "Not retried; check the Slack channel manually."
    ),
    "failed": "Flow 3 was not run, so no message was sent.",
}
DETAIL_PATTERNS = [
    (re.compile(r"^Flow 3 returned no Chat Output text\.$"), "Flow 3 returned no output text."),
    (re.compile(r"^Slack history unavailable \("), "Slack message history could not be checked."),
    (re.compile(r"^No message with source_event_id found"), "No matching message was found in the approved channel."),
    (re.compile(r"^Slack evidence lookup could not be completed\.$"), "Slack evidence lookup could not be completed."),
    (re.compile(r"^Flow 3 call ended without a response"), "Flow 3 call ended without a response (timeout or dropped connection)."),
    (re.compile(r"^Flow 3 returned HTTP (\d{3})\.$"), "Flow 3 returned HTTP {0}."),
    (re.compile(r"^Langflow rejected the Flow 3 request \(HTTP (\d{3})\)\.$"), "Langflow rejected the Flow 3 request (HTTP {0})."),
    (re.compile(r"^Could not connect to Langflow"), "Could not connect to Langflow."),
    (re.compile(r"^Langflow is not configured"), "Langflow is not configured on the server."),
]


def safe_details(row: dict) -> list[str]:
    details = []
    for value in (row.get("error"), row.get("evidence")):
        if not isinstance(value, str):
            continue
        for pattern, template in DETAIL_PATTERNS:
            match = pattern.match(value)
            if match:
                details.append(template.format(*match.groups()))
                break
    return details


def parse_time(value) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def public_execution(row: dict) -> dict:
    status = row.get("status")
    if status not in STATUS_SUMMARIES:
        status = "unknown"

    claimed_at = parse_time(row.get("claimed_at"))
    finished_at = parse_time(row.get("finished_at"))
    duration = None
    if claimed_at and finished_at and finished_at >= claimed_at:
        duration = round((finished_at - claimed_at).total_seconds(), 1)

    return {
        "source_event_id": row.get("source_event_id"),
        "status": status,
        "event_source_mode": row.get("event_source_mode"),
        "allowed_repository": row.get("allowed_repository"),
        "claimed_at": row.get("claimed_at"),
        "finished_at": row.get("finished_at"),
        "duration_seconds": duration,
        "summary": STATUS_SUMMARIES[status],
        "details": safe_details(row),
    }


@router.get("/{proposal_id}/executions")
async def get_execution_history(
    proposal_id: str,
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    x_ingest_token: str | None = Header(default=None),
):
    require_token(x_ingest_token, "CANDIDATE_INGEST_TOKEN")
    base_url, headers = supabase_settings()

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                f"{base_url}/rest/v1/automation_executions",
                headers=headers,
                params={
                    "proposal_id": f"eq.{proposal_id}",
                    "select": HISTORY_SELECT,
                    "order": "claimed_at.desc",
                    "limit": str(limit + 1),
                },
            )

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
                        "Execution history table is missing. Apply "
                        "backend/migrations/003_automation_executions.sql."
                    ),
                )
        response.raise_for_status()
        rows = response.json()

        if not isinstance(rows, list):
            raise ValueError("Unexpected Supabase response")

    except (httpx.HTTPError, ValueError):
        logger.exception("Failed to fetch execution history")
        raise HTTPException(
            status_code=502,
            detail="Failed to fetch execution history",
        )

    records = [public_execution(row) for row in rows[:limit] if isinstance(row, dict)]
    return {
        "data": records,
        "limit": limit,
        "has_more": len(rows) > limit,
    }
