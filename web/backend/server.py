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
from starlette.responses import HTMLResponse

import os
import time
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
    html_content = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>AI Survey Moderator</title>
        <style>
            body { font-family: Arial; margin: 40px; background: #f5f5f5; }
            .container { max-width: 600px; margin: 0 auto; background: white; padding: 40px; border-radius: 10px; }
            .button { background: #007bff; color: white; padding: 15px 30px; text-decoration: none; border-radius: 5px; display: inline-block; margin: 20px 0; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🤖 AI Survey Moderator</h1>
            <p>Welcome to our AI-powered focus group platform</p>
            <a href="/quick-demo" class="button">Quick Start Demo</a>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)

@app.get("/quick-demo", response_class=HTMLResponse)
async def quick_demo(request: Request):
    """Quick demo page"""
    html_content = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Quick Demo - AI Survey Moderator</title>
        <style>
            body { font-family: Arial; margin: 40px; background: #f5f5f5; }
            .container { max-width: 600px; margin: 0 auto; background: white; padding: 40px; border-radius: 10px; }
            .button { background: #007bff; color: white; padding: 15px 30px; text-decoration: none; border-radius: 5px; display: inline-block; margin: 20px 0; }
            input { width: 100%; padding: 10px; margin: 10px 0; border: 1px solid #ddd; border-radius: 5px; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🚀 Quick Start Demo</h1>
            <p>Enter your name to join the focus group session:</p>
            <form action="/join-session" method="post">
                <input type="text" name="participant_name"laceholder="Your Name" required>
                <br>
                <button type="submit" class="button">Join Focus Group</button>
            </form>
            <p><a href="/">← Back to Home</a></p>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)


#@app.get("/join/{survey_id}", response_class=HTMLResponse)
#async def join_page(request: Request, survey_id: str):
#    """Serve the join page for a specific survey"""
    # Get survey config from surveys.json
#    survey_config = get_survey_config(survey_id)
#
#    if not survey_config:
        # Survey not found in config - show error or use defaults
#        return templates.TemplateResponse("join.html", {
#            "request": request,
#            "survey_id": survey_id,
#            "survey": {"title": "Survey Session"},
#            "livekit_url": os.getenv("LIVEKIT_URL"),
#            "observer_enabled": False,
#            "survey_not_found": True,
#        })
#
    # Use config from surveys.json
#    observer_enabled = survey_config.get("has_observer", False)

#    return templates.TemplateResponse("join.html", {
#        "request": request,
#        "survey_id": survey_id,
#        "survey": {"title": survey_config.get("title", "Survey Session")},
#        "livekit_url": os.getenv("LIVEKIT_URL"),
#        "observer_enabled": observer_enabled,
#    })


@app.get("/survey/{survey_id}", response_class=HTMLResponse)
async def survey_page(request: Request, survey_id: str):
    """Serve the survey UI page"""
    return templates.TemplateResponse(
        request=request,
        name="survey.html",
        context={
            "survey_id": survey_id,
            "livekit_url": os.getenv("LIVEKIT_URL"),
        },
    )


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

@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    """Simple admin interface to start focus groups"""
    html_content = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Focus Group Admin</title>
        <style>
            body { font-family: Arial; margin: 40px; background: #f5f5f5; }
            .container { max-width: 600px; margin: 0 auto; background: white; padding: 40px; border-radius: 10px; }
            .button { background: #28a745; color: white; padding: 15px 30px; text-decoration: none; border-radius: 5px; display: inline-block; margin: 20px 0; border: none; cursor: pointer; }
            .info { background: #e7f3ff; padding: 15px; border-radius: 5px; margin: 20px 0; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1> Focus Group Admin</h1>
            <p>Start a new focus group session and get a participant link to share.</p>
            
            <form action="/create-session" method="post"                <label>Session Name (optional):</label><br>
                <input type="text" name="session_name" placeholder="Q4 Product Feedback" style="width: 100%; padding: 10px; margin: 10px 0; border: 1px solid #ddd; border-radius: 5px;"><br>
                
                <label>Number of Participants:</label><br>
                <select name="participant_count" style="width: 100%; padding: 10px; margin: 10px 0; border: 1px solid #ddd; border-radius: 5px;">
                    <option value="2">2 participants</option>
                    <option value="3">3 participants</option>
                    <option value="4">4 participants</option>
                    <option value="5">5 participants</option>
                </select><br>
                
                <button type="submit" class="button"> Start New Focus Group</button>
            </form>
            
            <div class="info">
                <strong>How it works:</strong><br>
                1. Click "Start New Focus Group" toreate a LiveKit room<br>
                2. Your AI agent will automatically join the room<br>
                3. You'll get a participant link to share with your focus group members
            </div>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)

@app.post("/create-session")
async def create_session(request: Request):
    """Create a new focus group session with actual LiveKit room"""
    form = await request.form()
    session_name = form.get("session_name", "Focus Group Session")
    participant_count = int(form.get("participant_count", "3"))
    
    # Generate session ID and room name
    session_id = f"session-{int(time.time())}"
    room_name = f"survey-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    
    # Create the actual LiveKit room and dispatch agent
    try:
        agent_ready = await ensure_room_and_agent(
            room_name, 
            max_participants=participant_count + 2,  # +2 for moderator + buffer
            has_observer=False
        )
        
        if not agent_ready:
            raise Exception("Failed to create room or dispatch agent")
        
        # Store in active surveys
        active_surveys[session_id] = {
            "survey_id": session_id,
            "room_name": room_name,
            "title": session_name,
            "max_participants": participant_count,
            "created_at": datetime.now().isoformat(),
            "participants": [],
            "has_observer": False,
        }
        
        # Success page with working LiveKit room
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Session Created!</title>
            <style>
                body {{ font-family: Arial; margin: 40px; background: #f5f5f5; }}
                .container {{ max-width: 600px; margin: 0 auto; background: white; padding: 40px; border-radius: 10px; }}
                .success {{ background: #d4edda; color: #155724; padding: 15px; border-radius: 5px; margin: 20px 0; }}
                .link-box {{ background: #e7f3ff; padding: 15px; border-radius: 5px; margin: 20px 0; }}
                .button {{ background: #007bff; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px; display: inline-block; margin: 10px 5px; }}
                .live-indicator {{ background: #28a745; color: white; padding: 8px 12px; border-radius: 15px; font-size: 12px; display: inline-block; }}
            </style>
        </head>
        <body>
            <div class="container">
                <h1> Focus Group Created & Live!</h1>
                
                <div class="success">
                    <strong>Session Details:</strong><br>
                    Name: {session_name}<br>
                    Expected Participants: {participant_count}<br>
                    Session ID: {session_id}<br>
                    LiveKit Room: {room_name}<br>
                    <span class="live-indicator">AI Agent Active</span>
                </div>
                
                <div class="link-box">
                    <strong>Share this link with participants:</strong><br>
                    <code>https://{request.headers.get('host', 'app.applwisd.com')}/join/{session_id}</code><br>
                    <small>Participants will join the LiveKit room with your AI agent and Anam avatar</small>
          </div>
                
                <a href="/admin" class="button">← Create Another Session</a>
                <a href="/join/{session_id}" class="button">🎯 Test Join as Participant</a>
            </div>
        </body>
        </html>
        """
        return HTMLResponse(content=html_content)
        
    except Exception as e:
        # Error page
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Session Creation Failed</title>
            <style>
                body {{ font-family: Arial; margin: 40px; background: #f5f5f5; }}
                .container {{ max-width: 600px; margin: 0 auto; background: white; padding: 40px; border-radius: 10px; }}
                .error {{ background: #f8d7da; color: #721c24; padding: 15px; border-radius: 5px; margin: 20px 0; }}
                .button {{ background: #007bff; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px; display: inline-block; margin: 10px 5px; }}
        </style>
        </head>
        <body>
            <div class="container">
                <h1>❌ Session Creation Failed</h1>
                <div class="error">
                    <strong>Error:</strong> {str(e)}<br>
                    Please ensure your AI agent is running and LiveKit credentials are configured.
                </div>
                <a href="/admin" class="button">← Try Again</a>
            </div>
        </body>
        </html>
        """
        return HTMLResponse(content=html_content)

@app.get("/join/{session_id}")
async def join_session_simple(request: Request, session_id: str):
    """Simple join page for sessions created through admin"""
    # Check if this session exists in our active_surveys
    session = active_surveys.get(session_id)
    
    if not session:
        return HTMLResponse("""
        <h1>Session Not Found</h1>
        <p>This session may have expired or the link is incorrect.</p>
        <a href="/admin">← Back to Admin</a>
        """, status_code=404)
    
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Join Focus Group</title>
        <style>
            body {{ font-family: Arial; margin: 40px; background: #f5f5f5; }}
            .container {{ max-width: 500px; margin: 0 auto; background: white; padding: 40px; border-radius: 10px; text-align: center; }}
            .button {{ background: #007bff; color: white; padding: 15px 30px; text-decoration: none; border-radius: 5px; display: inline-block; margin: 20px 0; border: nonecursor: pointer; }}
            input {{ width: 90%; padding: 15px; margin: 15px 0; border: 1px solid #ddd; border-radius: 5px; font-size: 16px; }}
            .session-info {{ background: #e7f3ff; padding: 15px; border-radius: 5px; margin: 20px 0; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🎯 Join Focus Group</h1>
            
            <div class="session-info">
                <strong>Session:</strong> {session['title']}<br>
                <strong>Room:</strong> {session['room_name']}<br>
                <strong>Status:</strong> Live & Ready
            </div>
            
            <p>Enter your name to join the discussion:</p>
            
            <form action="/join-now" method="post">
                <input type="hidden" name="survey_id" value="{session_id}">
                <input type="text" name="participant_name" placeholder="Your Name" required>
                <input type="hidden" name="role" value="participant">
                r>
                <button type="submit" class="button">🚀 Join Focus Group Now</button>
            </form>
            
            <p><small>You'll be connected to a live discussion with an AI moderator and other participants.</small></p>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)

@app.post("/join-now")
async def join_now_form(request: Request):
    """Handle form submission and redirect to the zoom-like survey UI."""
    import json as _json
    form = await request.form()
    survey_id = form.get("survey_id")
    participant_name = form.get("participant_name")
    role = form.get("role", "participant")

    try:
        session = active_surveys.get(survey_id)
        if not session:
            return HTMLResponse("<h1>Session not found</h1>", status_code=404)

        room_name = session["room_name"]
        await ensure_room_and_agent(
            room_name, max_participants=10, has_observer=False
        )

        token = generate_participant_token(room_name, participant_name)
        livekit_url = os.getenv("LIVEKIT_URL")

        token_js = _json.dumps(token)
        room_js = _json.dumps(room_name)
        url_js = _json.dumps(livekit_url)
        name_js = _json.dumps(participant_name)

        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Joining Focus Group...</title>
            <meta charset="utf-8">
            <style>
                body {{ font-family: Arial; margin: 40px; background: #f5f5f5; text-align: center; }}
                .container {{ max-width: 500px; margin: 0 auto; background: white; padding: 40px; border-radius: 10px; }}
            </style>
        </head>
        <body>
            <div class="container">
                <h1>Joining Focus Group...</h1>
                <p>Hi {participant_name}! Connecting you now.</p>
                <p><small>If you are not redirected in 2 seconds, <a href="/survey/{survey_id}">click here</a>.</small></p>
            </div>
            <script>
                sessionStorage.setItem('survey_token', {token_js});
                sessionStorage.setItem('survey_room', {room_js});
                sessionStorage.setItem('livekit_url', {url_js});
                sessionStorage.setItem('participant_name', {name_js});
                sessionStorage.setItem('participant_role', 'participant');
                sessionStorage.setItem('camera_enabled', 'true');
                window.location.replace('/survey/{survey_id}');
            </script>
        </body>
        </html>
        """
        return HTMLResponse(content=html_content)
    except Exception as e:
        return HTMLResponse(f"<h1>Error: {str(e)}</h1>", status_code=500)


@app.get("/room/{session_id}")
async def room_page(request: Request, session_id: str, participant: str = "Anonymous"):
    """LiveKit room page with embedded client"""
    session = active_surveys.get(session_id)
    if not session:
        return HTMLResponse("<h1>Session not found</h1>", status_code=404)
    
    room_name = session["room_name"] 
    token = generate_participant_token(room_name, participant)
    livekit_url = os.getenv("LIVEKIT_URL")
    
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Focus Group Room</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body {{ margin: 0; padding: 20px; font-family: Arial; background: #f0f0f0; }}
            .container {{ max-width: 1200px; margin: 0 auto; background: white; padding: 20px; border-radius: 10px; }}
            .loading {{ text-align: center; padding: 50px; }}
            #videoContainer {{ width: 100%; height: 600px; border: 1px solid #ddd; border-radius: 5px; background: #000; }}
            .controls {{ margin: 20px 0; text-align: center; }}
            .btn {{ background: #007bff; color: white; padding: 10px 20px; margin: 5px; border: none; border-radius: 5px; cursor: pointer; }}
            .status {{ padding: 10px; background: #e7f3ff; border-radius: 5px; margin: 10px 0; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🎯 Focus Group Room</h1>
            <p>Welcome {participant}!</p>
            
            <div class="status">
                <strong>Room:</strong> {room_name}<br>
                <strong>Status:</strong> <span id="status">Connecting...</span>
            </div>
            
            <div class="controls">
                <button class="btn" onclick="toggleAudio()">🎤 Toggle Audio</button>
                <button class="btn" onclick="toggleVideo()">📹 Toggle Video</button>
                <button class="btn" onclick="disconnect()">❌ Leave Room</button>
            </di       
            <div id="videoContainer"></div>
            
            <div id="participants">
                <h3>Participants:</h3>
                <div id="participantList">Connecting...</div>
            </div>
        </div>
        
        <script src="https://cdn.jsdelivr.net/npm/livekit-client@2.5.10/dist/livekit-client.umd.min.js"></script>
        <script>
            const LiveKit = window.LivekitClient;
            const room = new LiveKit.Room();
            let localAudioTrack;
            let localVideoTrack;
            
            document.getElementById('status').textContent = 'Initializing...';
            
            room.on(LiveKit.RoomEvent.Connected, () => {{
                console.log('Connected to room:', room.name);
                document.getElementById('status').textContent = 'Connected ✅';
                updateParticipants();
            }});
            
            room.on(LiveKit.RoomEvent.ParticipantConnected, (participant) => {{
                console.log('Participant joined:', participant.identity);
                updateParticipants();
            }});
            
            room.on(LiveKit.RoomEvent.TrackSubscribed, (track, publication, participant) => {{
                console.log('Track subscribed:', track.kind, 'from', participant.identity);
                if (track.kind === 'video') {{
                    const videoElement = track.attach();
                    videoElement.style.width = '300px';
                    videoElement.style.height = '200px';
                    videoElement.style.margin = '10px';
                    document.getElementById('videoContainer').appendChild(videoElement);
                }}
            }});
            
            function updateParticipants() {{
                const participants = Array.from(room.remoteParticipants.values()).map(p => p.identity);
                participants.push(room.localParticipant.identity);
                document.getElementById('participantList').innerHTML = participants.join(', ');
            }}
            
            async function toggleAudio() {{
                if (localAudioTrack) {{
                    await room.localParticipant.unpublishTrack(localAudioTrack);
                    localAudioTrack = null;
                }} else {{
                    localAudioTrack = await LiveKit.createLocalAudioTrack();
                    await room.localParticipant.publishTrack(localAudioTrack);
                }}
            }}
            
            async function toggleVideo() {{
                if (localVideoTrack) {{
                    await room.localParticipant.unpublishTrack(localVideoTrack);
                    localVideoTrack = null;
                }} else {{
                    localVideoTrack = await LiveKit.createLocalVideoTrack();
                    await room.localParticipant.publishTrack(localVideoTrack);
                    const videoElement = localVideoTrack.attach();
                    videoElement.style.width = '300px';
                    videoElement.style.height = '200px';
                    videoElement.style.margin = '10px';
                    document.getElementById('videoContainer').appendChild(videoElement);
                }}
            }}
            
            function disconnect() {{
                room.disconnect();
                window.close();
            }}
            
            // Make functions global
            window.toggleAudio = toggleAudio;
            window.toggleVideo = toggleVideo; 
            window.disconnect = disconnect;

           // Connect to room with better error handling
           const wsUrl = "{livekit_url}".replace('https://', 'wss://').replace('http://', 'ws://');
           room.connect(wsUrl, "{token}")
               .then(() => {{
                   console.log('✅ Successfully connected to LiveKit room');
                   document.getElementById('status').textContent = 'Connected ✅';
                   // Auto-enable audio for focus group
                   toggleAudio();
               }})
               .catch(err => {{
                   console.error('❌ Failed to connect:', err);
                   document.getElementById('status').textContent = '❌ Connection failed: ' + err.message;
                   // Try alternative connection
                   console.log('Trying direct connection...');
                   room.connect("{livekit_url}", "{token}").catch(e => console.error('Direct connection also failed:', e));
               }});
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)

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
