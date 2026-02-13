# Audio Issue Diagnosis

## Current Status

✅ **zoom_survey.py**: Running
✅ **agent.py dev**: Running
✅ **Local webhook server**: Healthy (port 8000)
✅ **ngrok**: Running and URL matches
❌ **Webhook events received**: 0 (This is the problem!)

## The Problem

Recall.ai is **NOT sending audio chunks** to your webhook. This means:
- Your audio in Zoom is NOT reaching the agent
- Agent responses are NOT being sent back to Zoom

## Possible Causes

### 1. Webhook Events Not Configured Properly

The most likely issue is that the new API structure for `recording_config.real_time_endpoints` might not be working as expected.

**Check the terminal running `zoom_survey.py` for:**
- Any errors during bot creation
- The bot ID that was created
- Whether it says "Real-time webhook endpoints: ENABLED"

### 2. Bot Not Actually in Meeting

**Check in Zoom:**
- Do you see "AI Survey Moderator" in the participants list?
- Is the bot's status "in call"?

### 3. Audio Format Issue

The new API might be sending audio in a different format or to a different event type than we expect.

## Immediate Debugging Steps

### Step 1: Check `zoom_survey.py` Output

Look at the terminal running `zoom_survey.py`. You should see:
```
[4/5] Launching Recall.ai bot to join Zoom...
✅ Recall bot created successfully! Bot ID: XXXXX
   Real-time webhook endpoints: ENABLED ✅
   Output media (bot audio): ENABLED ✅
```

**Copy the Bot ID** (you'll need it for Step 2)

### Step 2: Manually Check Bot Configuration

In a new terminal, run:
```bash
python3 -c "
import os
import requests
from dotenv import load_dotenv
import json

load_dotenv('.env.local')

api_key = os.getenv('RECALL_API_KEY')
bot_id = 'YOUR_BOT_ID_HERE'  # Replace with actual bot ID from Step 1

response = requests.get(
    f'https://us-west-2.recall.ai/api/v1/bot/{bot_id}',
    headers={'Authorization': f'Token {api_key}'}
)

data = response.json()
print(json.dumps(data, indent=2))
"
```

Look for:
- `recording_config.real_time_endpoints` - should have your webhook URL
- `status.code` - should be `in_call_recording` or `in_call_not_recording`
- `output_media` - should be configured

### Step 3: Test Webhook Manually

Test if Recall.ai can reach your webhook:
```bash
curl -X POST https://grainier-euna-occupiedly.ngrok-free.dev/webhook \
  -H "Content-Type: application/json" \
  -d '{
    "type": "bot.audio_chunk",
    "data": {
      "participant_id": "test",
      "audio": "dGVzdA==",
      "sample_rate": 16000
    }
  }'
```

Then check:
```bash
curl http://localhost:8000/health
```

You should see `events_received: 1`

### Step 4: Enable Debug Logging

Add this to your `.env.local`:
```
LOG_LEVEL=DEBUG
```

Then restart `zoom_survey.py` and look for detailed webhook logs.

## Quick Test

**Try speaking in Zoom and watch the terminal running `zoom_survey.py`.**

You should see lines like:
```
Received webhook #1: bot.audio_chunk
Received webhook #2: bot.audio_chunk
```

**If you DON'T see these**, the issue is that Recall.ai is not sending webhooks.

## Possible Solutions

### Solution 1: API Field Names Wrong

The `recording_config.real_time_endpoints` structure might be incorrect. We may need to use a different field name or structure.

### Solution 2: Webhook URL Issue

Even though ngrok is working, Recall.ai might not be able to reach it due to:
- Firewall
- ngrok free tier limitations
- URL validation issues

### Solution 3: Event Names Changed

The event names `bot.audio_chunk`, `bot.transcript` etc. might be different in the actual API.

## What to Share

Please share:
1. **Bot ID** from zoom_survey.py logs
2. **Full output** of the bot configuration from Step 2
3. **Any error messages** from zoom_survey.py terminal
4. **Screenshot** of Zoom showing the bot in participants list

This will help me identify exactly what's wrong and fix it.
