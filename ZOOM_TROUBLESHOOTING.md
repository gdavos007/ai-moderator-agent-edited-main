# Zoom Integration Troubleshooting Guide

## Problem: Bot joins but doesn't speak or ask questions

### Root Cause
The bot was created **without real-time audio features enabled**. By default, Recall.ai bots only record asynchronously - they don't stream audio in real-time or speak in meetings.

### Solution (NOW FIXED!)

The code has been updated to enable:
1. **Real-time transcription** - Streams participant audio to your webhook
2. **Output audio** - Allows bot to speak in the Zoom meeting
3. **Speech-to-text** - Tracks participant names and metadata

**File updated:** `src/zoom_bridge/recall_bot.py` (lines 73-93)

---

## How to Test the Fix

### Prerequisites

Make sure you have:
- ✅ Recall.ai account with $5 credit (Pay As You Go plan)
- ✅ ngrok installed and authenticated
- ✅ `.env.local` configured with `RECALL_API_KEY` and `RECALL_REGION=us-west-2`

### Step-by-Step Test

**Terminal 1: Start ngrok**
```bash
ngrok http 8000
```

Copy the `https://` URL (e.g., `https://abc123.ngrok-free.app`)

**Terminal 2: Start AI Agent**
```bash
python3 agent.py dev
```

Wait for: `✅ Agent started successfully!`

**Terminal 3: Launch Zoom Integration**
```bash
python3 zoom_survey.py \
  --zoom-url "YOUR_ZOOM_MEETING_URL" \
  --webhook-url "https://abc123.ngrok-free.app"
```

### What You Should See

**Old behavior (before fix):**
```
✅ Recall bot created successfully!
   Bot status: in_call_not_recording
```
- Bot joins silently
- No audio streaming
- No questions asked
- Just sits there muted

**New behavior (after fix):**
```
🚀 Creating bot with real-time features enabled...
   Webhook URL: https://abc123.ngrok-free.app
   Meeting URL: https://zoom.us/j/...
✅ Recall bot created successfully! Bot ID: abc123
   Bot status: in_call_recording
   Real-time transcription: ENABLED ✅
   Audio output in meeting: ENABLED ✅
   Webhook destination: https://abc123.ngrok-free.app
```

Then in your Zoom meeting, you should:
1. **Hear the AI moderator** say: "Hello everyone! I'm your AI survey moderator..."
2. **See webhook events** in Terminal 3 showing audio data
3. **Hear questions** being asked one by one

---

## Common Issues After Update

### Issue 1: "Bad Request - Invalid parameter: real_time_transcription"

**Possible causes:**
- Your Recall.ai account might be on a restricted free tier
- You haven't used your $5 credit yet (it might require manual activation)

**Solution:**
1. Check your Recall.ai dashboard: https://www.recall.ai/dashboard
2. Verify you're on "Pay As You Go" plan (shows $0.70/hour pricing)
3. If you're on a different tier, contact Recall.ai support

### Issue 2: "Webhook timeout" or "Connection refused"

**Possible causes:**
- ngrok not running
- Wrong webhook URL
- Firewall blocking ngrok

**Solution:**
```bash
# Test ngrok is accessible
curl https://your-ngrok-url.ngrok-free.app/health

# Should return:
# {"status":"healthy","events_received":0,...}
```

If this fails:
- Restart ngrok
- Check you copied the full HTTPS URL (not HTTP)
- Make sure port 8000 is not blocked

### Issue 3: Bot joins but still doesn't speak

**Possible causes:**
- Bot's microphone is muted in Zoom
- Zoom meeting has "Mute participants on entry" enabled
- Audio output failed to initialize

**Solution:**

1. **In Zoom meeting**, check if bot is muted:
   - Hover over "AI Survey Moderator" participant
   - If muted, click "Unmute"

2. **Check Zoom settings:**
   - Go to Zoom settings → Audio
   - Disable "Mute participants upon entry"
   - Restart the meeting

3. **Check bot status:**
   ```bash
   # In zoom_survey.py output, look for:
   Bot status: in_call_recording  # ✅ Good
   Bot status: in_call_not_recording  # ❌ Bad - audio not working
   ```

### Issue 4: Webhook receives events but agent doesn't respond

**Possible causes:**
- AudioForwarder not connected to LiveKit
- Agent not in the LiveKit room
- Audio format mismatch

**Solution:**

1. **Check agent is running:**
   ```bash
   # Terminal 2 should show:
   ✅ Agent started successfully!
   🎤 Listening in room: zoom-bridge-XXXXXX
   ```

2. **Check webhook handler:**
   ```bash
   # Terminal 3 should show:
   📡 Webhook received: participant_join
   📡 Webhook received: bot.audio_data
   ✅ Audio forwarded to LiveKit: participant-123
   ```

3. **Enable debug logging:**
   ```bash
   # In .env.local, add:
   LOG_LEVEL=DEBUG

   # Restart agent.py
   ```

---

## Understanding Real-Time Audio Flow

Here's what happens with the fix:

```
┌─────────────────┐
│  Zoom Meeting   │
│                 │
│  👤 Participant │──┐
│  🤖 Recall Bot  │  │
└─────────────────┘  │
                     │ 1. Participant speaks
                     │
                     ▼
┌──────────────────────────────────┐
│  Recall.ai (Cloud)               │
│  • Captures audio from meeting   │
│  • Separates by speaker (diarization) │
│  • Streams to webhook in real-time    │
└──────────────────────────────────┘
                     │
                     │ 2. Real-time audio webhook
                     │
                     ▼
┌──────────────────────────────────┐
│  Your Webhook (ngrok → FastAPI)  │
│  Port 8000                       │
└──────────────────────────────────┘
                     │
                     │ 3. Audio forwarded
                     │
                     ▼
┌──────────────────────────────────┐
│  LiveKit Room                    │
│  • AI Agent listening            │
│  • Processes via OpenAI STT      │
│  • Generates response via LLM    │
│  • Speaks via TTS                │
└──────────────────────────────────┘
                     │
                     │ 4. Agent audio response
                     │
                     ▼
┌──────────────────────────────────┐
│  AudioForwarder                  │
│  Sends audio to Recall.ai        │
└──────────────────────────────────┘
                     │
                     │ 5. Audio injected into meeting
                     │
                     ▼
┌─────────────────┐
│  Zoom Meeting   │
│                 │
│  👤 Participant │ ← Hears AI response!
│  🤖 Recall Bot  │
└─────────────────┘
```

---

## Cost Implications

With real-time features enabled:

**Per meeting costs:**
```
Recall.ai: $0.70/hour × duration
Storage: $0.05/hour after 7 days (if not auto-deleted)
OpenAI STT: $0.006/min × participants × duration
OpenAI LLM: ~$0.10 per participant
OpenAI TTS: ~$0.02 per meeting

Example (30 min, 10 participants):
Recall.ai: $0.35
OpenAI: ~$2.80
Total: ~$3.15 per meeting
```

**Your $5 credit covers:**
- ~1.4 hours of Recall.ai recording time
- Or ~1-2 complete surveys (with OpenAI costs)

**After credit runs out:**
You'll be charged $0.70/hour for Recall.ai + OpenAI costs

---

## Recommended: Switch to LiveKit Direct

If costs are a concern, **LiveKit Direct approach is 85% cheaper**:

```bash
# Instead of Zoom integration:
python3 join_survey_custom_names.py --participants 10

# Costs:
# - LiveKit: FREE (under 10k min/month)
# - OpenAI: ~$1.85 per survey
# - Total: ~$1.85 vs $12.35 with Recall.ai
```

**Use Recall.ai + Zoom ONLY IF:**
- Participants MUST join via Zoom (company policy)
- You already have Zoom infrastructure
- Budget allows $10-15 per survey

Otherwise, **use LiveKit Direct** - it's cheaper, more private, and works great!

---

## Testing Checklist

Before reporting issues, verify:

- [ ] ngrok is running and accessible (test with `curl`)
- [ ] Agent is running in Terminal 2
- [ ] Zoom meeting URL is correct (with password if needed)
- [ ] Webhook URL matches your ngrok URL exactly
- [ ] Bot shows "in_call_recording" status (not just "in_call_not_recording")
- [ ] Logs show "Real-time transcription: ENABLED ✅"
- [ ] Logs show "Audio output in meeting: ENABLED ✅"
- [ ] Bot is not muted in Zoom meeting
- [ ] You have sufficient Recall.ai credit ($0.70/hour minimum)

---

## Getting Help

If you're still having issues:

1. **Check logs** in all 3 terminals for error messages
2. **Share terminal output** showing the exact error
3. **Verify your Recall.ai plan** at https://www.recall.ai/dashboard
4. **Test webhook** with: `curl https://your-ngrok-url/health`
5. **Consider LiveKit Direct** as an alternative (simpler, cheaper)

---

## What Changed?

**File:** `src/zoom_bridge/recall_bot.py`

**Before (lines 76-79):**
```python
payload = {
    'meeting_url': self.zoom_meeting_url,
    'bot_name': self.bot_name,
}
```
❌ Bot joins but can't hear or speak

**After (lines 75-93):**
```python
payload = {
    'meeting_url': self.zoom_meeting_url,
    'bot_name': self.bot_name,
    'real_time_transcription': {
        'destination_url': self.webhook_url
    },
    'output_audio': {
        'enabled': True
    },
    'speech_to_text': {
        'provider': 'default'
    },
}
```
✅ Bot can hear participants and speak in meeting!

---

## Next Steps

1. **Test the updated code** following the steps above
2. **Report any errors** you encounter with full logs
3. **Consider LiveKit Direct** if Recall.ai costs are too high
4. **Enjoy your AI-moderated surveys!** 🎉
