from datetime import datetime, timezone
from uuid import uuid4
from typing import Literal

from pydantic import BaseModel, Field
from candidates_api import router as candidates_router

import os
from typing import Any

import httpx
from dotenv import load_dotenv

from fastapi import BackgroundTasks, HTTPException, FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware

from analysis_api import router as analysis_router
from automation_config_api import router as automation_config_router
from connection_verification_api import router as connection_verification_router
from automation_activation_api import router as automation_activation_router
from execution_history_api import router as execution_history_router
from destination_resolution_api import router as destination_resolution_router
from automation_execution import dispatch_new_event

load_dotenv()

WEBHOOK_URL = os.environ["LANGFLOW_WEBHOOK_URL"]
API_KEY = os.environ["LANGFLOW_API_KEY"]
SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_ANON_KEY = os.environ["SUPABASE_ANON_KEY"]


class ActivityInput(BaseModel):
    employee_id: str = Field(min_length=1)
    activity_type: Literal["pr_merged"]
    title: str = Field(min_length=1)
    repository: str = Field(min_length=1)


def transform_activity(activity: ActivityInput) -> dict:
    event_id = f"dashboard-{uuid4()}"

    return {
        "metadata": {
            "trigger_slug": "GITHUB_PULL_REQUEST_MERGED",
            "event_id": event_id
        },
        "data": {
            "actor": {
                "login": activity.employee_id
            },
            "title": activity.title,
            "repository": {
                "full_name": activity.repository
            },
            "timestamp": datetime.now(
                timezone.utc
            ).isoformat()
        }
    }


app = FastAPI(
    title="One For All API",
    description="Backend API for the Creator Dashboard",
    version="0.1.0"
)

app.include_router(analysis_router)
app.include_router(candidates_router)
app.include_router(automation_config_router)
app.include_router(connection_verification_router)
app.include_router(automation_activation_router)
app.include_router(execution_history_router)
app.include_router(destination_resolution_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173"
    ],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"]
)

@app.get("/api/health")
def health_check():
    return {
        "status": "ok",
        "service": "one-for-all-backend",
        "message": "FastAPI is running successfully"
    }



@app.post("/api/activities", status_code=202)
async def submit_activity(activity: ActivityInput, background_tasks: BackgroundTasks):

    event = transform_activity(activity)

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                WEBHOOK_URL,
                headers={
                    "x-api-key": API_KEY
                },
                json=event
            )

            response.raise_for_status()

    except httpx.TimeoutException:
        raise HTTPException(
            status_code=504,
            detail="Langflow request timed out"
        )

    except httpx.HTTPStatusError:
        raise HTTPException(
            status_code=502,
            detail="Langflow rejected the request"
        )

    except httpx.RequestError:
        raise HTTPException(
            status_code=502,
            detail="Cannot connect to Langflow"
        )

    background_tasks.add_task(dispatch_new_event, event["metadata"]["event_id"])

    return {
        "status": "accepted",
        "event_id": event["metadata"]["event_id"],
        "message": "Activity forwarded to Langflow"
    }


@app.get("/api/activities")
async def get_activities(
    limit: int = Query(default=20, ge=1, le=100)
):
    url = f"{SUPABASE_URL}/rest/v1/activity_events"

    headers = {
        "apikey": SUPABASE_ANON_KEY,
        "Authorization": f"Bearer {SUPABASE_ANON_KEY}",
        "Accept": "application/json"
    }

    params = {
        "select": (
            "id,employee_id,tool,action,object,"
            "project,timestamp,source_event_id,"
            "metadata,created_at"
        ),
        "order": "created_at.desc",
        "limit": limit
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                url,
                headers=headers,
                params=params
            )

            response.raise_for_status()

            activities = response.json()

    except httpx.TimeoutException:
        raise HTTPException(
            status_code=504,
            detail="Supabase request timed out"
        )

    except httpx.HTTPStatusError:
        raise HTTPException(
            status_code=502,
            detail="Supabase rejected the request"
        )

    except httpx.RequestError:
        raise HTTPException(
            status_code=502,
            detail="Cannot connect to Supabase"
        )

    except ValueError:
        raise HTTPException(
            status_code=502,
            detail="Invalid JSON response from Supabase"
        )

    if not isinstance(activities, list):
        raise HTTPException(
            status_code=502,
            detail="Unexpected Supabase response format"
        )

    return {
        "total": len(activities),
        "data": activities
    }

