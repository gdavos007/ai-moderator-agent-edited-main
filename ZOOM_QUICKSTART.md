# Zoom Integration - Quick Start

Get your AI moderator working with Zoom in 10 minutes.

## Prerequisites

- Recall.ai account (sign up at https://recall.ai/)
- ngrok installed (`brew install ngrok` on macOS)
- Existing `.env.local` configured

## Setup (One-Time)

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Add Recall.ai API Key

Edit `.env.local`:

```bash
RECALL_API_KEY=your_recall_api_key_here
```

## Running a Zoom Survey

### Terminal 1: Start Webhook Tunnel

```bash
ngrok http 8000
```

Copy the `https://` URL (e.g., `https://abc123.ngrok.io`)

### Terminal 2: Start AI Agent

```bash
python agent.py
```

### Terminal 3: Start Zoom Bridge

```bash
python zoom_survey.py \
  --zoom-url "https://zoom.us/j/YOUR_MEETING_ID" \
  --webhook-url "https://abc123.ngrok.io"
```

Replace:
- `YOUR_MEETING_ID` - Your Zoom meeting URL
- `abc123.ngrok.io` - Your ngrok URL from Terminal 1

### Done!

When participants join Zoom, they'll automatically appear in LiveKit with their Zoom names.

The agent will greet them and start the survey.

## Stopping

Press `Ctrl+C` in Terminal 3. Cleanup is automatic.

## Troubleshooting

**Bot stuck in waiting room?**
- Go to Zoom as host
- Click "Participants"
- Admit "AI Survey Moderator"

**Webhook not working?**
- Check ngrok is running
- Use the HTTPS URL (not HTTP)
- Test: `curl https://your-ngrok.ngrok.io/health`

**Participant not showing up?**
- Check logs in Terminal 3 for "NEW ZOOM PARTICIPANT JOINED"
- Check Terminal 2 for "Added participant"
- Verify `.env.local` has correct LIVEKIT credentials

## Full Documentation

See [ZOOM_SETUP.md](ZOOM_SETUP.md) for complete guide, troubleshooting, and cost estimates.

## Example Output

```
🎯 NEW ZOOM PARTICIPANT JOINED:
   Name: Sarah Johnson
   ID: zoom_abc123
✅ Created LiveKit connection for Sarah Johnson
   The AI agent will now see them as: 'Sarah Johnson'
```

That's it! 🎉
