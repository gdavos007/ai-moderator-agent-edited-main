# AI Survey - Web Join Portal

A Zoom-like web interface for participants to join survey sessions. This is an **optional add-on** that provides a user-friendly alternative to the terminal-based `start_fresh_survey.py` script.

## Features

- **Zoom-like UI** - Familiar join experience participants already know
- **Waiting Room** - Countdown timer for scheduled surveys
- **Device Testing** - Microphone check before joining
- **Real-time Audio Visualization** - Visual feedback during survey
- **Mobile Friendly** - Responsive design works on all devices
- **ngrok Support** - Easy tunnel setup for external access

## Quick Start

### 1. Start the Agent (Terminal 1)
```bash
SURVEY_ID=quantitative_example python agent.py dev
```

### 2. Start the Web Server (Terminal 2)
```bash
cd web
./scripts/start_web_server.sh
```

### 3. Open in Browser
- Local: http://localhost:8080/quick-start
- Share with participants (same network): http://YOUR_IP:8080/quick-start

### For External Participants (Internet Access)

Use ngrok to create a public URL:

```bash
# Option A: Run server with tunnel (recommended)
cd web
./scripts/start_with_tunnel.sh

# Option B: Start ngrok separately
ngrok http 8080
```

Share the ngrok URL (e.g., `https://abc123.ngrok-free.app/quick-start`) with participants.

## Directory Structure

```
web/
├── backend/
│   └── server.py           # FastAPI web server
├── frontend/
│   └── css/
│       └── zoom-like.css   # Zoom-like styling
├── templates/
│   ├── index.html          # Landing page
│   ├── join.html           # Pre-join screen with device testing
│   └── survey.html         # Survey session UI
├── scripts/
│   ├── start_web_server.sh     # Start server only
│   └── start_with_tunnel.sh    # Start server + ngrok
└── README.md
```

## Usage Comparison

### Current Terminal-Based Workflow
```bash
# Terminal 1
SURVEY_ID=quantitative_example python agent.py dev

# Terminal 2
python3 start_fresh_survey.py --participants 2
# Enter names, get join links
```

### Web-Based Workflow (This Add-on)
```bash
# Terminal 1
SURVEY_ID=quantitative_example python agent.py dev

# Terminal 2
cd web && ./scripts/start_web_server.sh

# Share URL with participants - they enter their own names
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Landing page with survey code input |
| `/quick-start` | GET | Quick start - creates survey and shows join page |
| `/join/{survey_id}` | GET | Join page for specific survey |
| `/survey/{survey_id}` | GET | Survey session UI |
| `/api/survey/create` | POST | Create new survey session |
| `/api/survey/join` | POST | Get token to join survey |
| `/api/survey/{id}/status` | GET | Check survey status |

## Requirements

Install dependencies:
```bash
pip install fastapi uvicorn python-dotenv livekit livekit-api jinja2 python-multipart
```

For tunneling (optional):
```bash
# macOS
brew install ngrok

# Then authenticate
ngrok config add-authtoken YOUR_TOKEN
```

## Configuration

The web server uses the same `.env.local` file as the main agent:
- `LIVEKIT_URL` - LiveKit server URL
- `LIVEKIT_API_KEY` - API key
- `LIVEKIT_API_SECRET` - API secret

## Participant Flow

1. **Receive URL** - Participant gets survey URL (via email, SMS, etc.)
2. **Open Join Page** - See survey title, enter name
3. **Test Microphone** - Automatic mic check with visual feedback
4. **Join Survey** - Click "Join Survey" button
5. **Survey Session** - Interact with AI moderator
6. **Leave** - Click "Leave" when done

## Notes

- The agent (`agent.py`) must be running before participants can join
- This add-on does NOT modify any existing agent code
- You can use both methods (terminal and web) interchangeably
- For production use, deploy to a cloud server instead of using ngrok
