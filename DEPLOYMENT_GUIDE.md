# AI Community Moderator Agent - Deployment Guide

## Overview

The AI Community Moderator Agent is a multi-modal voice AI assistant that moderates community discussions in real-time. It joins LiveKit rooms automatically, greets participants, and helps facilitate productive conversations.

---

## What the Agent Does

### Core Capabilities

1. **Automatic Room Joining**: Automatically joins any new room created in your LiveKit Cloud project
2. **Voice Interaction**:
   - Listens to participant speech using Speech-to-Text (STT)
   - Processes conversations using a Language Model (LLM)
   - Responds with natural voice using Text-to-Speech (TTS)
3. **Moderation**:
   - Welcomes participants and introduces itself
   - Facilitates discussions
   - Maintains conversation flow
4. **Real-time Processing**: Uses Voice Activity Detection (VAD) and noise cancellation for clear communication

### Technical Stack

- **Platform**: LiveKit Cloud
- **Language**: Python 3.12
- **Framework**: LiveKit Agents 1.2.16
- **AI Models** (All OpenAI):
  - STT: `gpt-4o-transcribe`
  - LLM: `gpt-4o-mini`
  - TTS: `gpt-4o-mini-tts` with "ash" voice
- **Additional Features**:
  - Silero VAD for voice activity detection
  - Background Voice Cancellation (BVC) for noise reduction
  - Multilingual turn detection

---

## Project Structure

```
ai-moderator-agent/
├── agent.py                    # Main entry point
├── config/
│   └── agent_config.py         # Configuration settings
├── src/
│   ├── moderator_agent.py      # Agent implementation
│   └── utils.py                # Utility functions
├── .env.local                  # Environment variables (API keys)
├── requirements.txt            # Python dependencies
├── Dockerfile                  # Docker configuration
├── .dockerignore              # Files to exclude from Docker
└── livekit.toml               # LiveKit Cloud configuration
```

---

## Complete Setup Guide

### Prerequisites

1. **LiveKit Cloud Account**: https://cloud.livekit.io
2. **OpenAI API Key**: https://platform.openai.com/account/api-keys
3. **Python 3.12+** installed on your machine
4. **LiveKit CLI** installed:
   ```bash
   brew install livekit-cli  # macOS
   # OR
   curl -sSL https://get.livekit.io/cli | bash  # Linux
   ```

### Step 1: Environment Setup

1. **Authenticate with LiveKit Cloud**:
   ```bash
   lk cloud auth
   ```
   This opens a browser to link your LiveKit Cloud project.

2. **Configure Environment Variables**:

   Your `.env.local` file should contain:
   ```bash
   LIVEKIT_URL=wss://your-project.livekit.cloud
   LIVEKIT_API_KEY=your_api_key
   LIVEKIT_API_SECRET=your_api_secret
   OPENAI_API_KEY=your_openai_key
   ```

### Step 2: Install Dependencies

1. **Create Virtual Environment**:
   ```bash
   python3.12 -m venv ai_moderator_agent
   source ai_moderator_agent/bin/activate  # macOS/Linux
   ```

2. **Install Python Packages**:
   ```bash
   pip install --only-binary :all: av  # Install pre-built PyAV
   pip install -r requirements.txt
   ```

3. **Download Model Files**:
   ```bash
   python agent.py download-files
   ```

4. **Fix SSL Certificates** (macOS only):
   ```bash
   /Applications/Python\ 3.12/Install\ Certificates.command
   ```

### Step 3: Local Testing (Optional)

Test the agent locally before deploying:

```bash
python agent.py dev
```

The agent will:
- Connect to LiveKit Cloud
- Wait for room requests
- Join rooms automatically when participants connect

### Step 4: Deploy to LiveKit Cloud

1. **Initial Deployment** (first time only):
   ```bash
   lk agent create --secrets-file .env.local
   ```

   This will:
   - Create a Dockerfile (if needed)
   - Build a Docker image
   - Upload to LiveKit Cloud
   - Deploy the agent
   - Create `livekit.toml` with agent ID

2. **Update Deployment** (for code changes):
   ```bash
   lk agent deploy --secrets-file .env.local
   ```

   This does a rolling deployment:
   - Builds new Docker image
   - Deploys alongside existing version
   - Routes new sessions to new version
   - Gracefully shuts down old instances

### Step 5: Verify Deployment

1. **Check Agent Status**:
   ```bash
   lk agent status
   ```

   Expected output:
   ```
   ┌─────────────────┬─────────────────┬─────────┬─────────┐
   │ ID              │ Version         │ Status  │ Replicas│
   ├─────────────────┼─────────────────┼─────────┼─────────┤
   │ CA_xxxxx        │ v20251028...    │ Running │ 1/1/1   │
   └─────────────────┴─────────────────┴─────────┴─────────┘
   ```

2. **View Live Logs**:
   ```bash
   lk agent logs --log-type deploy
   ```

---

## Starting a Community Discussion

### Method 1: LiveKit Agents Playground (Easiest)

1. **Open Playground**:
   - Visit: https://agents-playground.livekit.io

2. **Connect to Your Project**:
   - Click "Connect"
   - Select your project: "AI_Agent_Moderator"
   - Click "Connect"

3. **Wait for Agent** (10-20 seconds for cold start):
   - Agent status will show "Agent connected"
   - Agent will automatically greet you

4. **Start Speaking**:
   - Click microphone button if needed
   - Speak naturally
   - Agent will respond and moderate the conversation

### Method 2: Custom Web Application

1. **Use LiveKit Client SDK** in your web app:
   ```javascript
   import { Room } from 'livekit-client';

   const room = new Room();
   await room.connect('wss://your-project.livekit.cloud', token);
   ```

2. **Agent Auto-Joins**:
   - When you create/join a room, the agent automatically dispatches
   - No explicit agent invitation needed
   - Agent introduces itself within 10-20 seconds

### Method 3: Programmatic Room Creation

Create a test room using the Python API:

```python
from livekit import api

livekit_api = api.LiveKitAPI()
room = await livekit_api.room.create_room(
    api.CreateRoomRequest(
        name="community-discussion-1",
        max_participants=10,
    )
)
```

The agent will automatically join this room.

---

## Key Configuration Files

### 1. `agent.py` (Main Entry Point)

**Purpose**: Starts the agent worker and defines the entry point

**Key Code**:
```python
async def entrypoint(ctx: agents.JobContext):
    """Called when agent joins a room"""
    # Create agent session with AI models
    session = await create_moderator_session(
        ctx=ctx,
        instructions=MODERATOR_INSTRUCTIONS,
        stt_model="gpt-4o-transcribe",
        llm_model="gpt-4o-mini",
        tts_model="gpt-4o-mini-tts",
        tts_voice="ash",
    )

    # Generate greeting
    await session.generate_reply(
        instructions="Greet participants and introduce yourself..."
    )
```

**Note**: No `agent_name` parameter = automatic dispatch enabled

### 2. `src/moderator_agent.py` (Agent Implementation)

**Purpose**: Implements the agent class and session creation

**Key Components**:

```python
class CommunityModeratorAgent(Agent):
    """Extends LiveKit Agent base class"""
    def __init__(self, instructions: str):
        super().__init__(instructions=instructions)
        self.participant_tracker = {}
        self.moderation_events = []

async def create_moderator_session(...):
    """Creates and configures the agent session"""
    # Create agent instance
    moderator = CommunityModeratorAgent(instructions=instructions)

    # Create session with AI models
    session = AgentSession(
        stt=openai.STT(model=stt_model),
        llm=openai.LLM(model=llm_model),
        tts=openai.TTS(model=tts_model, voice=tts_voice),
        vad=silero.VAD.load(),
    )

    # Start session
    await session.start(
        room=ctx.room,
        agent=moderator,  # Pass agent instance
        room_input_options=RoomInputOptions(
            noise_cancellation=noise_cancellation.BVC(),
        ),
    )
```

### 3. `config/agent_config.py` (Configuration)

**Purpose**: Centralized configuration management

**Settings**:
- Model selection (STT, LLM, TTS)
- Voice settings
- Temperature (LLM creativity)
- Log level

### 4. `Dockerfile` (Docker Configuration)

**Purpose**: Defines the container environment for deployment

```dockerfile
FROM python:3.12-slim
WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY agent.py .
COPY config/ ./config/
COPY src/ ./src/

# Run the agent
CMD ["python", "agent.py", "start"]
```

### 5. `.dockerignore` (Docker Exclusions)

**Purpose**: Excludes unnecessary files from Docker build

**Excluded**:
- Virtual environment (`ai_moderator_agent/`, `venv/`)
- Python cache (`__pycache__/`, `*.pyc`)
- Tests and documentation
- Git files
- IDE settings

---

## Agent Behavior

### On Startup

1. Agent worker registers with LiveKit Cloud
2. Waits for room creation/participant joining
3. Receives job dispatch request
4. Joins the room automatically

### On Room Join

1. Connects to the room
2. Initializes AI models (STT, LLM, TTS, VAD)
3. Generates welcome greeting:
   - "Hello! I'm the AI moderator here to help..."
   - Introduces role and responsibilities
4. Begins listening for participant speech

### During Discussion

1. **Listen**: Detects when participants speak (VAD)
2. **Transcribe**: Converts speech to text (STT)
3. **Understand**: Processes with language model (LLM)
4. **Respond**: Generates speech response (TTS)
5. **Facilitate**: Helps keep discussion productive

### On Participant Events

- Logs when participants join/leave
- Tracks participant activity
- Adjusts behavior based on room dynamics

---

## Monitoring & Management

### View Agent Status

```bash
lk agent status
```

Shows:
- Agent ID
- Current version
- Running status
- CPU/Memory usage
- Number of replicas
- Deployment timestamp

### View Live Logs

```bash
# Deployment logs
lk agent logs --log-type deploy

# Build logs
lk agent logs --log-type build
```

### Restart Agent

```bash
lk agent restart
```

Restarts without interrupting active sessions.

### Rollback to Previous Version

```bash
lk agent rollback
```

Instantly reverts to the previous deployed version.

### Update Environment Variables

```bash
lk agent update --secrets-file .env.local
```

Updates secrets and restarts the agent.

---

## Deployment Architecture

### Docker Build Process

1. **Source Code** → `lk agent deploy`
2. **Docker Image Built** with:
   - Python 3.12 base image
   - Application code
   - Dependencies from requirements.txt
3. **Image Pushed** to LiveKit Cloud registry
4. **Agent Deployed** to us-east region
5. **Auto-scaling** enabled (min 0, max based on plan)

### Cold Start Behavior

On **free tier**, agents scale to 0 when idle:
- First participant triggers "cold start" (10-20 seconds)
- Agent container starts
- Models load
- Agent joins room and greets

### Automatic Dispatch

**How it works**:
- `agent_name` parameter is **NOT set** in `WorkerOptions`
- This enables automatic dispatch mode
- Agent joins **ALL new rooms** automatically
- No explicit dispatch API call needed

**Alternative (Explicit Dispatch)**:
- Set `agent_name="CommunityModerator"` in code
- Use `lk dispatch create` or API to dispatch to specific rooms

---

## Common Operations

### Update Agent Code

1. **Modify code** locally
2. **Test locally** (optional):
   ```bash
   python agent.py dev
   ```
3. **Deploy update**:
   ```bash
   lk agent deploy --secrets-file .env.local
   ```
4. **Verify**:
   ```bash
   lk agent status
   ```

### Add New AI Model Provider

1. **Install plugin**:
   ```bash
   pip install livekit-plugins-anthropic
   ```
2. **Update code**:
   ```python
   from livekit.plugins import anthropic
   llm=anthropic.LLM(model="claude-3-5-sonnet-20241022")
   ```
3. **Add API key** to `.env.local`
4. **Deploy**:
   ```bash
   lk agent deploy --secrets-file .env.local
   ```

### Enable Additional Features

**Turn Detection**:
```python
from livekit.plugins.turn_detector.multilingual import MultilingualModel

session = AgentSession(
    ...
    turn_detection=MultilingualModel(),
)
```

**Vision (Multimodal)**:
```python
await session.start(
    ...
    room_input_options=RoomInputOptions(
        video_enabled=True,
    ),
)
```

---

## Troubleshooting

### Agent Not Joining Rooms

**Check**:
1. Agent status: `lk agent status` (should show "Running")
2. Logs: `lk agent logs --log-type deploy`
3. Verify `agent_name` is NOT set (for auto-dispatch)

### SSL Certificate Errors (macOS)

**Fix**:
```bash
/Applications/Python\ 3.12/Install\ Certificates.command
```

### PyAV Build Errors

**Fix**: Use pre-built wheels:
```bash
pip install --only-binary :all: av
```

### Agent Crashes on Start

**Check logs**:
```bash
lk agent logs --log-type deploy | grep ERROR
```

Common issues:
- Missing API keys in `.env.local`
- Invalid API key format
- Incorrect model names

---

## Production Considerations

### Scaling

- **Free Tier**: 0-1 replicas, cold starts
- **Paid Plans**: 1+ min replicas, no cold starts
- Auto-scales based on concurrent sessions

### Cost Optimization

1. **Use efficient models**:
   - `gpt-4o-mini` vs `gpt-4o` (10x cheaper)
2. **Monitor usage**:
   - LiveKit Cloud dashboard
   - OpenAI usage dashboard
3. **Set appropriate temperature**:
   - Lower = more focused, faster, cheaper
   - Higher = more creative, slower, costlier

### Security

1. **Protect API Keys**:
   - Never commit `.env.local` to git
   - Use LiveKit secrets management
2. **Validate Input**:
   - Set max turn length
   - Implement content filtering if needed
3. **Monitor Usage**:
   - Set OpenAI usage limits
   - Monitor LiveKit quotas

---

## Next Steps

### Enhance the Agent

1. **Add Custom Tools**: Implement moderation functions
2. **Add RAG**: Connect to knowledge bases
3. **Add Workflows**: Multi-step conversation flows
4. **Add Memory**: Persist conversation context

### Integrate with Your App

1. **Web Frontend**: Use LiveKit client SDKs
2. **Mobile Apps**: iOS/Android SDKs
3. **Telephony**: SIP integration for phone calls
4. **Webhooks**: React to room events

### Advanced Features

1. **Multi-Agent Systems**: Multiple specialized agents
2. **Virtual Avatars**: Add video avatars
3. **Real-time Translation**: Multi-language support
4. **Analytics**: Track conversation metrics

---

## Support Resources

- **LiveKit Docs**: https://docs.livekit.io/agents
- **LiveKit Discord**: https://livekit.io/discord
- **OpenAI Docs**: https://platform.openai.com/docs
- **GitHub Issues**: https://github.com/anthropics/claude-code/issues

---

## Deployed Agent Details

**Current Configuration**:
- **Agent ID**: `CA_KJkgkznW5PD8`
- **Project**: aiagentmoderator
- **Region**: US East B
- **Version**: v20251028032824
- **Status**: Running ✅
- **URL**: wss://aiagentmoderator-bzu1wgs1.livekit.cloud

**API Keys Required**:
- LiveKit API Key & Secret
- OpenAI API Key

**Models Used**:
- STT: gpt-4o-transcribe (OpenAI)
- LLM: gpt-4o-mini (OpenAI)
- TTS: gpt-4o-mini-tts/ash (OpenAI)
- VAD: Silero
- Noise Cancellation: BVC

---

*Document Generated: October 28, 2025*
*Agent Version: v20251028032824*
