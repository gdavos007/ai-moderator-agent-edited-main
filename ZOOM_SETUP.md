# Zoom Integration Setup Guide (Phase 1: Recall.ai)

This guide explains how to use your AI moderator agent with Zoom meetings using Recall.ai as the bridge.

## Table of Contents

1. [Overview](#overview)
2. [Prerequisites](#prerequisites)
3. [Installation](#installation)
4. [Configuration](#configuration)
5. [Running a Zoom Survey](#running-a-zoom-survey)
6. [How It Works](#how-it-works)
7. [Troubleshooting](#troubleshooting)
8. [Cost Estimate](#cost-estimate)
9. [Limitations](#limitations)

---

## Overview

The Zoom integration allows your AI moderator agent to:

- ✅ Join Zoom meetings automatically as a bot participant
- ✅ Capture participant names directly from Zoom
- ✅ Conduct surveys with Zoom participants using the same agent logic
- ✅ Export responses with real Zoom participant names
- ✅ Works with existing `agent.py` without code changes

### Architecture

```
Zoom Meeting → Recall.ai Bot → Webhook Server → LiveKit Room → AI Agent
                (Captures)      (Forwards)       (Creates)     (Moderates)
```

---

## Prerequisites

### 1. Recall.ai Account

**Sign up**: https://recall.ai/

1. Create a free account
2. Get your API key from the dashboard
3. Note: Free tier has limitations, production use requires paid plan

**Pricing** (as of 2024):
- Development: Free for testing
- Production: ~$0.02-0.05 per minute per participant

### 2. Publicly Accessible Webhook URL

Recall.ai needs to send webhooks to your server. For development, use **ngrok**:

**Install ngrok**:
```bash
# macOS
brew install ngrok

# Or download from: https://ngrok.com/download
```

**Sign up for ngrok**:
1. Create account at https://ngrok.com/
2. Get your auth token
3. Configure: `ngrok config add-authtoken YOUR_TOKEN`

### 3. LiveKit Agent Running

Your existing LiveKit agent must be running:

```bash
python agent.py
```

The agent will automatically detect participants as they join from Zoom.

---

## Installation

### 1. Install Dependencies

```bash
# Install new dependencies for Zoom bridge
pip install -r requirements.txt
```

This installs:
- `fastapi` - Webhook server
- `uvicorn` - ASGI server
- `requests` - Recall.ai API client
- `numpy` - Audio processing

### 2. Configure Environment Variables

Copy `.env.example` to `.env.local` if you haven't already:

```bash
cp .env.example .env.local
```

Edit `.env.local` and add your Recall.ai API key:

```bash
# Recall.ai Configuration
RECALL_API_KEY=your_recall_api_key_here
```

**Keep your existing variables**:
- `LIVEKIT_URL`
- `LIVEKIT_API_KEY`
- `LIVEKIT_API_SECRET`
- `OPENAI_API_KEY`

---

## Configuration

### Required Settings

Edit `.env.local`:

```bash
# LiveKit (already configured)
LIVEKIT_URL=wss://your-project.livekit.cloud
LIVEKIT_API_KEY=your_api_key
LIVEKIT_API_SECRET=your_api_secret

# OpenAI (already configured)
OPENAI_API_KEY=sk-your_key

# Recall.ai (NEW - add this)
RECALL_API_KEY=your_recall_api_key_from_dashboard
```

---

## Running a Zoom Survey

### Step-by-Step Guide

#### 1. Start ngrok Tunnel (for webhook)

In a **new terminal window**:

```bash
ngrok http 8000
```

You'll see output like:

```
Forwarding  https://abc123.ngrok.io -> http://localhost:8000
```

**Copy the HTTPS URL** (e.g., `https://abc123.ngrok.io`) - you'll need it in step 3.

#### 2. Start LiveKit Agent

In a **second terminal window**:

```bash
python agent.py
```

The agent will wait for participants to join.

#### 3. Start Zoom Bridge

In a **third terminal window**:

```bash
python zoom_survey.py \
  --zoom-url "https://zoom.us/j/YOUR_MEETING_ID" \
  --webhook-url "https://abc123.ngrok.io"
```

**Replace**:
- `YOUR_MEETING_ID` - Your Zoom meeting URL (get from Zoom app)
- `https://abc123.ngrok.io` - Your ngrok URL from step 1

**Example**:
```bash
python zoom_survey.py \
  --zoom-url "https://zoom.us/j/123456789?pwd=abc123" \
  --webhook-url "https://abc123.ngrok.io"
```

#### 4. What Happens Next

You'll see logs showing:

```
[1/4] Creating LiveKit room...
✅ Created LiveKit room: zoom-survey-20240115-143022

[2/4] Initializing audio forwarder...
✅ AudioForwarder initialized

[3/4] Setting up webhook handler...
✅ Webhook handler ready

[4/4] Launching Recall.ai bot to join Zoom...
✅ Recall bot created! Bot ID: bot_abc123
   Bot status: joining
   Bot status: in_waiting_room
   Bot status: in_call_not_recording
✅ Bot successfully joined Zoom meeting!

⏳ Waiting for participants to join Zoom meeting...
```

#### 5. Participants Join Zoom

When someone joins the Zoom meeting, you'll see:

```
🎯 NEW ZOOM PARTICIPANT JOINED:
   Name: Sarah Johnson
   ID: zoom_abc123
✅ Created LiveKit connection for Sarah Johnson
   The AI agent will now see them as: 'Sarah Johnson'
```

In the **agent terminal**, you'll see:

```
Found existing participant: Sarah Johnson
✅ Added participant: Sarah Johnson (total: 1)
```

#### 6. Survey Begins

The AI agent will:
1. Greet participants: "Hello! I'm your AI survey moderator..."
2. Wait 30 seconds for everyone to get ready
3. Start asking questions: "Sarah Johnson, [question text]"
4. Capture responses with correct names

#### 7. Stop the Survey

Press `Ctrl+C` in the zoom_survey.py terminal.

Cleanup happens automatically:
- Recall bot leaves Zoom
- LiveKit room deleted
- Webhook server stops

---

## How It Works

### Name Mapping Flow

1. **Zoom Participant Joins**
   - User joins Zoom with display name "John Doe"

2. **Recall.ai Detection**
   - Bot detects new participant
   - Sends webhook:
     ```json
     {
       "type": "bot.participant_join",
       "data": {
         "participant": {
           "id": "zoom_123",
           "name": "John Doe"
         }
       }
     }
     ```

3. **Webhook Handler**
   - Receives event at `/webhook`
   - Extracts name: "John Doe"
   - Calls `AudioForwarder.add_zoom_participant()`

4. **Audio Forwarder**
   - Creates LiveKit token with identity: "John Doe"
   - Connects to LiveKit room as "John Doe"
   - Publishes audio track

5. **AI Agent**
   - Detects new participant: "John Doe"
   - Adds to `ParticipantManager`
   - Asks questions: "John Doe, what is your opinion?"

6. **CSV Export**
   - Saves responses with name: "John Doe"

### No Agent Code Changes Required

Your existing code in `agent.py` and `src/moderator_agent.py` works unchanged because:

- Agent only sees LiveKit participants
- Bridge creates LiveKit participants with Zoom names
- Agent doesn't know (or care) that participants are from Zoom

---

## Troubleshooting

### Bot Stuck in Waiting Room

**Problem**: Recall bot shows `in_waiting_room` status

**Solution**:
1. Go to Zoom meeting as host
2. Click "Participants"
3. Find "AI Survey Moderator" in waiting room
4. Click "Admit"

**Prevention**: Disable waiting room for the meeting:
- Zoom Settings → Meeting → Waiting Room → Off

### Webhook Not Receiving Events

**Problem**: No participant join events

**Check**:
```bash
# Test webhook is accessible
curl https://your-ngrok-url.ngrok.io/health

# Should return:
{"status": "healthy", "events_received": 0, ...}
```

**Common Issues**:
- ngrok not running → Restart: `ngrok http 8000`
- Wrong URL in command → Use HTTPS ngrok URL
- Firewall blocking → Check firewall settings

### Participant Not Appearing in LiveKit

**Problem**: Participant joins Zoom but agent doesn't see them

**Check Logs**:

1. **zoom_survey.py terminal** - Should show:
   ```
   🎯 NEW ZOOM PARTICIPANT JOINED: John Doe
   ✅ Created LiveKit connection for John Doe
   ```

2. **agent.py terminal** - Should show:
   ```
   Found existing participant: John Doe
   ✅ Added participant: John Doe
   ```

**If step 1 fails**: Webhook issue (see above)

**If step 2 fails**: LiveKit connection issue
- Check `LIVEKIT_URL` in `.env.local`
- Verify LiveKit credentials

### Audio Not Working

**Problem**: Participant's audio not captured

**Note**: This is a **known limitation** in Phase 1.

Recall.ai primarily supports:
- ✅ Participant detection (names)
- ✅ Audio transcription
- ❌ Real-time audio streaming (limited)

For **full audio integration**, you need Phase 2 (Custom Zoom SDK).

**Workaround for Phase 1**:
- Have participants also join the LiveKit room directly
- Or rely on Recall's transcription (see `webhook_handler.py:_handle_transcription`)

---

## Cost Estimate

### Recall.ai Costs

**Pricing** (subject to change):
- Per-minute charge: ~$0.02-0.05 per participant
- Bot connection fee: ~$0.01 per minute

**Example Survey**:
- Duration: 60 minutes
- Participants: 5
- Cost: 60 min × 5 people × $0.03 = **$9 per survey**

**Monthly at Scale**:
- 100 surveys/month = ~$900/month
- 500 surveys/month = ~$4,500/month

**Break-even**: If you run >22 surveys/month, Phase 2 (Custom SDK) is cheaper.

### Other Costs

- LiveKit: Free for development (paid for production scale)
- OpenAI: ~$0.05-0.15 per participant (STT/LLM/TTS)
- ngrok: Free tier OK for testing (paid for production)

---

## Limitations

### Phase 1 (Current) Limitations

1. **Audio Streaming**: Limited real-time audio injection
   - Participants hear agent through LiveKit, not Zoom
   - Consider hybrid approach (participants join both)

2. **Latency**: Additional ~200-500ms
   - Zoom → Recall → Webhook → LiveKit → Agent
   - Acceptable for surveys, less ideal for real-time conversations

3. **Recall.ai Dependency**: Relies on third-party service
   - Subject to their uptime and API changes
   - Ongoing costs per survey

4. **Bot Visibility**: Appears as participant in Zoom
   - Shows as "AI Survey Moderator" in participant list
   - May confuse some participants

### When to Consider Phase 2 (Custom SDK)

Switch to Phase 2 if:
- ✅ Running >22 surveys/month (cost savings)
- ✅ Need <100ms latency
- ✅ Need full bidirectional audio in Zoom
- ✅ Want to avoid third-party dependencies
- ✅ Need custom audio processing

**Phase 2 Requirements**:
- Zoom Meeting SDK license ($1,800/year)
- 4-8 weeks development time
- Advanced audio processing knowledge

---

## Next Steps

### Test Your Integration

1. Create a test Zoom meeting
2. Follow "Running a Zoom Survey" steps above
3. Invite a friend to join as participant
4. Verify their Zoom name appears correctly
5. Complete a short survey

### Production Checklist

Before using in production:

- [ ] Recall.ai paid plan configured
- [ ] Production webhook URL (not ngrok)
- [ ] LiveKit production deployment
- [ ] Error monitoring configured
- [ ] Backup/recording enabled
- [ ] Cost budgeting approved
- [ ] Participant privacy consent obtained

### Getting Help

- **Recall.ai Docs**: https://docs.recall.ai/
- **LiveKit Docs**: https://docs.livekit.io/
- **Issues**: Check logs in all three terminals
- **Support**: Contact Recall.ai support for API issues

---

## Quick Reference

### Start Survey (3 Terminals)

**Terminal 1** - Webhook tunnel:
```bash
ngrok http 8000
# Copy the https:// URL
```

**Terminal 2** - AI Agent:
```bash
python agent.py
```

**Terminal 3** - Zoom Bridge:
```bash
python zoom_survey.py \
  --zoom-url "https://zoom.us/j/YOUR_MEETING_ID" \
  --webhook-url "https://YOUR_NGROK_URL.ngrok.io"
```

### Stop Survey

Press `Ctrl+C` in Terminal 3 (zoom_survey.py)

Cleanup is automatic.

---

## Conclusion

You've successfully integrated Zoom with your AI moderator agent!

**Key Benefits**:
- ✅ Participants use familiar Zoom interface
- ✅ Real participant names automatically captured
- ✅ No changes to your existing agent code
- ✅ Fast implementation (Phase 1)

**Remember**: This is Phase 1 (Recall.ai). If costs become prohibitive or you need lower latency, consider implementing Phase 2 (Custom Zoom SDK).

Happy surveying! 🎉
