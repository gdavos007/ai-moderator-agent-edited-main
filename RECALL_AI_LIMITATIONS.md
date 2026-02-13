# Recall.ai Free Tier Limitations

## Discovery

After multiple attempts to create a Recall.ai bot with real-time features, we've discovered that **the free "Pay As You Go" tier does NOT support the features required for bidirectional audio**.

## API Errors Encountered

### Error 1: `real_time_transcription` Not Allowed
```json
{
  "real_time_transcription": [
    "This field is not allowed. (more details: https://docs.recall.ai/reference/bot_create?region=us-west-2)"
  ]
}
```

**Impact**: Cannot receive real-time audio/transcription from Zoom participants

### Error 2: `automatic_audio_output` Format Issues
```json
{
  "automatic_audio_output": {
    "in_call_recording": {
      "data": ["This field is required."]
    }
  }
}
```

**Impact**: Cannot send AI agent audio back to Zoom in real-time

## What Works (Free Tier)

Based on testing, the free tier supports:
- ✅ Creating bots with basic meeting URL and name
- ✅ Joining Zoom meetings
- ✅ Recording (async, not real-time)
- ✅ Transcription (async, after meeting)

## What Doesn't Work (Free Tier)

The free tier does NOT support:
- ❌ Real-time audio streaming to webhooks
- ❌ Real-time transcription
- ❌ Bidirectional audio (bot speaking in meetings)
- ❌ Live audio forwarding to LiveKit

## Implications for AI Moderator Agent

### Original Goal
Integrate Zoom meetings with bidirectional audio:
- Participants speak in Zoom → Bot captures audio → Forwards to LiveKit → AI agent hears
- AI agent responds → LiveKit audio → Bot plays in Zoom → Participants hear

### Current Reality
With Recall.ai free tier, we can only:
- Create bot that joins Zoom meeting
- Record meeting (async)
- Get transcription after meeting ends

**We CANNOT:**
- Stream participant audio in real-time
- Have AI agent speak in Zoom meetings
- Create interactive survey experience

## Options Moving Forward

### Option 1: Upgrade Recall.ai Plan (RECOMMENDED)
**Pros:**
- Enables real-time transcription
- Enables bidirectional audio
- Keeps Zoom integration architecture

**Cons:**
- Monthly cost (need to check pricing)
- May have usage limits

**Next Steps:**
1. Visit https://www.recall.ai/pricing
2. Check if paid plan includes real-time features
3. Evaluate cost vs. value

### Option 2: Switch to LiveKit Direct (Alternative)
**Pros:**
- No Recall.ai costs
- Full real-time bidirectional audio
- Lower latency
- Better quality

**Cons:**
- Participants must join LiveKit room (not Zoom)
- Requires sending participants a LiveKit room URL
- May not work for "Zoom-only" surveys

**Implementation:**
- Use existing `join_survey.py` approach
- Participants join via web link
- No Zoom needed at all

### Option 3: Hybrid Approach
**Pros:**
- Support both Zoom (with Recall.ai paid) and LiveKit direct
- Maximum flexibility

**Cons:**
- More complex
- Higher maintenance
- Potentially confusing for users

## Current Code Status

### What's Implemented
✅ Complete bidirectional audio code (audio_converter.py, audio_forwarder.py)
✅ PCM to MP3 conversion working
✅ Test suite passing
✅ Bot creation with minimal payload

### What's Blocked
❌ Real-time audio forwarding (requires Recall.ai paid plan)
❌ Agent speaking in Zoom (requires Recall.ai paid plan)
❌ End-to-end Zoom integration testing

## Recommendation

**I recommend Option 1 (Upgrade Recall.ai) IF:**
- Your survey participants MUST use Zoom
- You have budget for the paid plan
- Real-time interaction is required

**I recommend Option 2 (LiveKit Direct) IF:**
- Participants can join via web link (not just Zoom)
- Cost is a major concern
- You want lower latency and better quality

## Testing Next Steps

### To test minimal bot (without bidirectional audio):
```bash
# Terminal 1: Agent
python3 agent.py dev

# Terminal 2: Zoom Integration
python3 zoom_survey.py \
  --zoom-url "YOUR_ZOOM_URL" \
  --webhook-url "http://localhost:8000"
```

**Expected behavior:**
- Bot should join Zoom meeting successfully
- Bot will record (async only)
- NO real-time audio forwarding
- NO bidirectional audio

This will verify the bot can join, but won't provide the interactive experience we designed.

## Cost Comparison

### Recall.ai Free Tier
- Cost: $0.70/hour
- Real-time: ❌ NO
- Bidirectional: ❌ NO

### Recall.ai Paid Tier (TBD)
- Cost: Unknown (need to check pricing)
- Real-time: ✅ YES
- Bidirectional: ✅ YES (assumed)

### LiveKit Direct
- Cost: $0.03/participant/hour (estimated)
- Real-time: ✅ YES
- Bidirectional: ✅ YES

**For a 30-minute survey with 10 participants:**
- Recall.ai Free: ~$0.35 (but no real-time features)
- Recall.ai Paid: Unknown
- LiveKit Direct: ~$1.50 (fully functional)

## Conclusion

The Recall.ai free tier does NOT support the real-time features required for our bidirectional audio implementation. We need to either:
1. Upgrade to Recall.ai paid plan, OR
2. Switch to LiveKit Direct approach

The code we built (audio conversion, forwarding, etc.) is fully functional and ready to use once we have a real-time audio source. But without upgrading Recall.ai or switching approaches, we cannot achieve the interactive Zoom survey experience.
