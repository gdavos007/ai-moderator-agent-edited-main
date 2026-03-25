#!/usr/bin/env python3
"""
Survey Web Server - Zoom-like Join Experience

This server provides a web-based interface for participants to join surveys.
It works alongside the existing agent.py - participants join via browser
instead of the terminal-based start_fresh_survey.py script.

Usage:
    Terminal 1: SURVEY_ID=xxx python agent.py dev
    Terminal 2: cd web && python backend/server.py

    Then share the URL with participants (use ngrok for external access)
"""

import os
import sys
import json
import asyncio
import logging
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
import secrets

# Add parent directories to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
import uvicorn

# Load environment variables
load_dotenv(Path(__file__).parent.parent.parent / ".env.local")

# LiveKit imports
from livekit import api

app = FastAPI(title="AI Survey - Join Portal")
logger = logging.getLogger(__name__)

# Setup paths
WEB_DIR = Path(__file__).parent.parent
FRONTEND_DIR = WEB_DIR / "frontend"
TEMPLATES_DIR = WEB_DIR / "templates"
CONFIG_DIR = WEB_DIR / "config"
SURVEYS_CONFIG_FILE = CONFIG_DIR / "surveys.json"


def load_surveys_config() -> dict:
    """Load survey configuration from JSON file"""
    if SURVEYS_CONFIG_FILE.exists():
        with open(SURVEYS_CONFIG_FILE, "r") as f:
            return json.load(f)
    return {}


def get_survey_observer_password(survey_id: str) -> Optional[str]:
    """Get observer password for a specific survey"""
    config = load_surveys_config()
    survey_config = config.get(survey_id, {})
    return survey_config.get("observer_password")


def is_observer_enabled(survey_id: str) -> bool:
    """Check if observer is enabled for a specific survey"""
    config = load_surveys_config()
    survey_config = config.get(survey_id, {})
    return survey_config.get("has_observer", False)


def get_survey_config(survey_id: str) -> Optional[dict]:
    """Get full survey configuration from surveys.json"""
    config = load_surveys_config()
    return config.get(survey_id)

# Mount static files
app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

# Setup templates
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# In-memory storage for active surveys (in production, use Redis or database)
active_surveys = {}


class SurveyConfig(BaseModel):
    """Survey configuration for web-based joining"""
    survey_id: str
    room_name: str
    scheduled_time: Optional[datetime] = None
    max_participants: int = 8
    title: str = "AI Survey Session"
    description: str = ""
    duration_minutes: int = 20


class JoinRequest(BaseModel):
    """Request to join a survey"""
    survey_id: str
    participant_name: str
    role: str = "participant"  # "participant" or "observer"
    observer_password: Optional[str] = None


def get_livekit_credentials():
    """Get LiveKit credentials from environment"""
    url = os.getenv("LIVEKIT_URL")
    api_key = os.getenv("LIVEKIT_API_KEY")
    api_secret = os.getenv("LIVEKIT_API_SECRET")

    if not all([url, api_key, api_secret]):
        raise ValueError("Missing LiveKit credentials in environment")

    return url, api_key, api_secret


def generate_participant_token(room_name: str, participant_name: str, identity: str = None) -> str:
    """Generate a LiveKit access token for a participant"""
    url, api_key, api_secret = get_livekit_credentials()

    # Create identity from name if not provided (same logic as start_fresh_survey.py)
    if not identity:
        identity = participant_name.lower().replace(" ", "_")
        identity = ''.join(c for c in identity if c.isalnum() or c == '_')

    # Create token
    token = api.AccessToken(api_key, api_secret)
    token.with_identity(identity)
    token.with_name(participant_name)
    token.with_grants(api.VideoGrants(
        room_join=True,
        room=room_name,
        can_publish=True,
        can_subscribe=True,
        can_publish_data=True,
    ))

    # Set token expiration (2 hours)
    token.with_ttl(timedelta(hours=2))

    return token.to_jwt()


# ── Per-room locking to prevent duplicate agent dispatch ──────────────────────
_room_locks: defaultdict = defaultdict(asyncio.Lock)
_dispatched_rooms: set = set()


async def ensure_room_and_agent(
    room_name: str,
    *,
    max_participants: int = 10,
    has_observer: bool = False,
) -> bool:
    """Create a LiveKit room and dispatch exactly one agent, atomically.

    Acquires a per-room asyncio.Lock so concurrent requests for the same room
    cannot both decide to create/dispatch.  Room creation is idempotent (no
    list_rooms precheck).  Agent presence is verified under the lock before
    dispatching.

    Returns True on success, False on error.
    """
    lock = _room_locks[room_name]
    logger.info(f"[ensure] Acquiring lock for room {room_name}")

    async with lock:
        logger.info(f"[ensure] Lock acquired for room {room_name}")

        # Fast path: already dispatched in a previous request
        if room_name in _dispatched_rooms:
            logger.info(f"[ensure] Agent already dispatched to {room_name} (cache hit)")
            return True

        url, api_key, api_secret = get_livekit_credentials()
        livekit_api = api.LiveKitAPI(url, api_key, api_secret)

        # ── Step 1: Create room (idempotent — LiveKit returns existing room) ─
        room_metadata = {
            "expected_participants": max_participants,
            "has_observer": has_observer,
        }
        try:
            await livekit_api.room.create_room(
                api.CreateRoomRequest(
                    name=room_name,
                    empty_timeout=3600,
                    max_participants=max_participants + 2 + (1 if has_observer else 0),
                    metadata=json.dumps(room_metadata),
                )
            )
            logger.info(f"[ensure] Room {room_name} created/confirmed (metadata={room_metadata})")
        except Exception as e:
            logger.error(f"[ensure] Room creation failed for {room_name}: {e}")
            return False

        # ── Step 2: Check for existing agent participants ────────────────────
        try:
            resp = await livekit_api.room.list_participants(
                api.ListParticipantsRequest(room=room_name)
            )
            participants = resp.participants or []
            # kind=4 is PARTICIPANT_KIND_AGENT in LiveKit SDK
            agent_count = sum(
                1 for p in participants
                if p.kind == 4 or p.identity.startswith("agent")
            )
            if agent_count > 0:
                logger.info(
                    f"[ensure] Agent already present in {room_name} "
                    f"({agent_count} agent(s)) — skipping dispatch"
                )
                _dispatched_rooms.add(room_name)
                return True
        except Exception as e:
            logger.warning(f"[ensure] Could not list participants for {room_name}: {e}")
            # Proceed to dispatch anyway — worst case LiveKit rejects duplicate

        # ── Step 3: Dispatch agent exactly once ──────────────────────────────
        try:
            await livekit_api.agent_dispatch.create_dispatch(
                api.CreateAgentDispatchRequest(
                    room=room_name,
                    agent_name="survey-moderator",
                )
            )
            _dispatched_rooms.add(room_name)
            logger.info(f"[ensure] Agent dispatched to {room_name}")
            return True
        except Exception as e:
            logger.error(f"[ensure] Agent dispatch failed for {room_name}: {e}")
            return False


# ============================================================
# API Routes
# ============================================================

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    """Serve the main landing page"""
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/join/{survey_id}", response_class=HTMLResponse)
async def join_page(request: Request, survey_id: str):
    """Serve the join page for a specific survey"""
    # Get survey config from surveys.json
    survey_config = get_survey_config(survey_id)

    if not survey_config:
        # Survey not found in config - show error or use defaults
        return templates.TemplateResponse("join.html", {
            "request": request,
            "survey_id": survey_id,
            "survey": {"title": "Survey Session"},
            "livekit_url": os.getenv("LIVEKIT_URL"),
            "observer_enabled": False,
            "survey_not_found": True,
        })

    # Use config from surveys.json
    observer_enabled = survey_config.get("has_observer", False)

    return templates.TemplateResponse("join.html", {
        "request": request,
        "survey_id": survey_id,
        "survey": {"title": survey_config.get("title", "Survey Session")},
        "livekit_url": os.getenv("LIVEKIT_URL"),
        "observer_enabled": observer_enabled,
    })


@app.get("/survey/{survey_id}", response_class=HTMLResponse)
async def survey_page(request: Request, survey_id: str):
    """Serve the survey UI page"""
    return templates.TemplateResponse("survey.html", {
        "request": request,
        "survey_id": survey_id,
        "livekit_url": os.getenv("LIVEKIT_URL"),
    })


@app.post("/api/survey/create")
async def create_survey(config: SurveyConfig):
    """Create a new survey session"""
    survey_id = config.survey_id or f"survey-{secrets.token_hex(6)}"

    # Store survey config
    active_surveys[survey_id] = {
        "survey_id": survey_id,
        "room_name": config.room_name or f"survey-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
        "scheduled_time": config.scheduled_time.isoformat() if config.scheduled_time else None,
        "max_participants": config.max_participants,
        "title": config.title,
        "description": config.description,
        "duration_minutes": config.duration_minutes,
        "created_at": datetime.now().isoformat(),
        "participants": [],
    }

    return JSONResponse({
        "success": True,
        "survey_id": survey_id,
        "join_url": f"/join/{survey_id}",
    })


@app.post("/api/survey/join")
async def join_survey(join_request: JoinRequest):
    """Generate token for participant or observer to join survey"""
    survey_id = join_request.survey_id
    participant_name = join_request.participant_name.strip()
    role = join_request.role.lower()

    if not participant_name or len(participant_name) < 2:
        raise HTTPException(status_code=400, detail="Name must be at least 2 characters")

    if role not in ["participant", "observer"]:
        raise HTTPException(status_code=400, detail="Invalid role. Must be 'participant' or 'observer'")

    # Validate observer password if joining as observer
    if role == "observer":
        expected_password = get_survey_observer_password(survey_id)
        if expected_password is None:
            raise HTTPException(status_code=403, detail="Observer access not configured for this survey")
        if join_request.observer_password != expected_password:
            raise HTTPException(status_code=403, detail="Invalid observer password")

    # Get survey config from surveys.json
    survey_config = get_survey_config(survey_id)
    has_observer = survey_config.get("has_observer", False) if survey_config else False

    # Get or create active survey session
    if survey_id not in active_surveys:
        room_name = f"survey-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        active_surveys[survey_id] = {
            "survey_id": survey_id,
            "room_name": room_name,
            "title": survey_config.get("title", "AI Survey Session") if survey_config else "AI Survey Session",
            "participants": [],
            "has_observer": has_observer,
        }

    survey = active_surveys[survey_id]
    room_name = survey["room_name"]

    # Create room + dispatch agent atomically (per-room lock prevents duplicate dispatch)
    agent_ready = await ensure_room_and_agent(
        room_name, max_participants=10, has_observer=has_observer,
    )
    if not agent_ready:
        raise HTTPException(status_code=500, detail="Could not start survey agent. Make sure agent.py is running.")

    # Generate identity based on role
    base_identity = participant_name.lower().replace(" ", "_")
    base_identity = ''.join(c for c in base_identity if c.isalnum() or c == '_')

    if role == "observer":
        identity = f"observer_{base_identity}"
    else:
        identity = base_identity

    # Generate token
    token = generate_participant_token(room_name, participant_name, identity)

    # Track participant/observer
    survey["participants"].append({
        "name": participant_name,
        "role": role,
        "joined_at": datetime.now().isoformat(),
    })

    return JSONResponse({
        "success": True,
        "token": token,
        "room_name": room_name,
        "livekit_url": os.getenv("LIVEKIT_URL"),
        "role": role,
    })


@app.get("/api/survey/{survey_id}/status")
async def get_survey_status(survey_id: str):
    """Get the current status of a survey"""
    survey = active_surveys.get(survey_id)

    if not survey:
        return JSONResponse({
            "exists": False,
            "status": "not_found"
        })

    # Check if scheduled
    scheduled_time = survey.get("scheduled_time")
    if scheduled_time:
        scheduled_dt = datetime.fromisoformat(scheduled_time)
        now = datetime.now()

        if now < scheduled_dt:
            time_until = scheduled_dt - now
            return JSONResponse({
                "exists": True,
                "status": "scheduled",
                "scheduled_time": scheduled_time,
                "seconds_until_start": int(time_until.total_seconds()),
                "title": survey.get("title", "AI Survey"),
            })

    return JSONResponse({
        "exists": True,
        "status": "active",
        "title": survey.get("title", "AI Survey"),
        "participant_count": len(survey.get("participants", [])),
    })


@app.get("/api/surveys")
async def list_surveys():
    """List all available surveys from config"""
    config = load_surveys_config()
    surveys = []
    for survey_id, survey_config in config.items():
        if survey_id.startswith("_"):  # Skip comments/metadata
            continue
        surveys.append({
            "survey_id": survey_id,
            "title": survey_config.get("title", survey_id),
            "has_observer": survey_config.get("has_observer", False),
            "join_url": f"/join/{survey_id}",
        })
    return JSONResponse({"surveys": surveys})


@app.get("/api/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "ok", "timestamp": datetime.now().isoformat()}


# ============================================================
# Quick Start Route (for easy testing)
# ============================================================

@app.get("/quick-start", response_class=HTMLResponse)
async def quick_start(request: Request):
    """Quick start - redirects to the default 'quick-start' survey from config"""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/join/quick-start", status_code=302)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Survey Web Server")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind to")
    parser.add_argument("--port", type=int, default=8080, help="Port to bind to")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload")
    args = parser.parse_args()

    print("\n" + "="*60)
    print("🌐 AI Survey Web Server")
    print("="*60)
    print(f"\n📍 Local URL: http://localhost:{args.port}")
    print(f"📍 Network URL: http://0.0.0.0:{args.port}")
    print("\n💡 For external access, use ngrok:")
    print(f"   ngrok http {args.port}")
    print("\n" + "="*60 + "\n")

    uvicorn.run(
        "server:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )
