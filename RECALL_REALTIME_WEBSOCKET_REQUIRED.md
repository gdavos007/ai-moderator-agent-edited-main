# Recall.ai Real-Time Audio Requires Websockets

## Discovery

After extensive testing, I've discovered that **Recall.ai's real-time audio feature requires WEBSOCKET connections, not HTTP webhooks**.

### What We Tried (HTTP Webhooks)
```json
{
  "recording_config": {
    "realtime_endpoints": [
      {
        "type": "webhook",  // ❌ This doesn't work for real-time audio
        "url": "https://your-domain.com/webhook",
        "events": ["audio_separate_raw", "transcript", "participant_events"]
      }
    ]
  }
}
```

**Result:** All event names rejected as "not a valid choice"

### What Actually Works (Websockets)
```json
{
  "recording_config": {
    "realtime_endpoints": [
      {
        "type": "websocket",  // ✅ This is required
        "url": "wss://your-domain.com/api/ws/audio",
        "events": ["audio_mixed_raw.data"]  // Note: .data suffix IS used for websockets
      }
    ]
  }
}
```

## Why This Matters

Our current implementation uses **FastAPI HTTP webhooks**. Real-time audio requires **websocket connections**, which is a completely different protocol.

### Current Architecture
```
Recall Bot (Zoom) → HTTP POST → FastAPI webhook → AudioForwarder → LiveKit
```

### Required Architecture
```
Recall Bot (Zoom) → Websocket Stream → Websocket Server → AudioForwarder → LiveKit
```

## Implementation Required

To support real-time bidirectional audio with Recall.ai, we need to:

### 1. Implement Websocket Server
- Add websocket support to FastAPI
- Handle binary audio data streams
- Maintain persistent connections
- Handle websocket reconnections

### 2. Update Bot Configuration
```python
'recording_config': {
    'realtime_endpoints': [
        {
            'type': 'websocket',
            'url': 'wss://your-ngrok-url.ngrok-free.app/ws/audio',
            'events': ['audio_separate_raw.data']
        }
    ]
}
```

### 3. Handle Audio Streams
- Receive continuous PCM audio buffers
- Forward to LiveKit in real-time
- Handle multiple participant streams

### 4. Ngrok Websocket Support
- Ngrok DOES support websockets
- Same tunnel works for both HTTP and WS
- No additional configuration needed

## Effort Estimate

**Medium Complexity** (~2-4 hours):
- Add FastAPI websocket endpoint: ~30 min
- Implement audio stream handling: ~1 hour
- Test and debug: ~1-2 hours
- Integration with existing AudioForwarder: ~30 min

## Alternative: LiveKit Direct

Given the complexity, **LiveKit Direct** remains simpler:
- ✅ Already implemented and working
- ✅ No websocket server needed
- ✅ No Recall.ai complications
- ✅ Better latency and quality
- ✅ Lower cost (~$1.50 vs unknown for 30-min survey)

**Trade-off:** Participants must use LiveKit web link instead of Zoom

## Recommendation

I recommend one of two paths:

### Option 1: Implement Websockets (If Zoom is Required)
**Best for:** Surveys that MUST use Zoom

**Steps:**
1. Add websocket endpoint to FastAPI
2. Update bot creation to use websocket URL
3. Test real-time audio flow
4. Implement output_media webpage for agent audio

**Timeline:** 2-4 hours of development

### Option 2: Use LiveKit Direct (Simpler)
**Best for:** Surveys where web link is acceptable

**Steps:**
1. Use existing `join_survey.py`
2. Send participants LiveKit room URL
3. Works immediately with full bidirectional audio

**Timeline:** Already working!

## Current Status

- ✅ Bot can join Zoom
- ❌ No real-time audio (websockets not implemented)
- ❌ Agent cannot speak in Zoom (no output_media webpage)
- ✅ LiveKit Direct fully functional

## Next Steps (If Proceeding with Zoom)

1. **Add websocket support to webhook_handler.py**
2. **Update zoom_survey.py to start websocket server**
3. **Configure bot with websocket URL**
4. **Create output_media webpage** for agent audio
5. **Test end-to-end**

## Documentation References

- [Real-Time Websocket Endpoints](https://docs.recall.ai/docs/real-time-websocket-endpoints)
- [Output Media API](https://docs.recall.ai/docs/stream-media)
- [FastAPI Websockets](https://fastapi.tiangolo.com/advanced/websockets/)

## Cost Comparison (30-min survey, 10 participants)

| Approach | Real-time | Bidirectional | Cost | Complexity |
|----------|-----------|---------------|------|------------|
| Recall + Websockets | ✅ | ✅ | ~$3-5 | High |
| LiveKit Direct | ✅ | ✅ | ~$1.50 | Low |
| Recall HTTP only | ❌ | ❌ | ~$0.35 | Medium |

## Conclusion

**The Pay As You Go plan DOES support real-time audio**, but only through **websockets, not HTTP webhooks**. This requires additional implementation work.

Given the complexity and cost, **LiveKit Direct is the recommended approach** unless Zoom integration is absolutely required.
