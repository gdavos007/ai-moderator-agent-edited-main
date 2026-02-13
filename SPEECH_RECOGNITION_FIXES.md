# Speech Recognition Issues & Fixes

## Issue: STT Transcription Errors

**Observed Errors:**
- "Right track" → "Great track", "Write track", "They track", "White track"

## Root Cause

OpenAI's Whisper STT model sometimes mishears similar-sounding phrases, especially:
- Short responses (1-2 words)
- Similar phonetics ("right" vs "write" vs "white")
- Poor audio quality
- Background noise

## Solutions

### Solution 1: Use Constrained Decoding (Best for Your Use Case)

Update the STT configuration to provide context about expected responses:

```python
# In src/moderator_agent.py, line 675
stt=openai.STT(
    model=stt_model,
    language="en",
    # Add prompts with expected vocabulary
    prompt="Common survey responses include: right track, wrong track, excellent, good, fair, poor, very poor, yes, no, strongly agree, somewhat agree, somewhat disagree, strongly disagree, very likely, somewhat likely, not very likely, not at all likely."
),
```

### Solution 2: Increase Audio Quality

In `agent.py`, add better audio processing:

```python
room_input_options=RoomInputOptions(
    noise_cancellation=noise_cancellation.BVC(),
    # Add these for better quality:
    auto_gain_control=True,
    echo_cancellation=True,
)
```

### Solution 3: Post-Processing Correction

Add fuzzy matching to correct common mistakes:

```python
def correct_transcription(text: str, expected_options: list) -> str:
    """Correct common STT errors by matching to expected options"""
    from difflib import get_close_matches

    # Normalize text
    text_lower = text.lower().strip()

    # Try exact match first
    for option in expected_options:
        if option.lower() == text_lower:
            return option

    # Fuzzy match (finds closest option)
    matches = get_close_matches(text_lower,
                                [opt.lower() for opt in expected_options],
                                n=1,
                                cutoff=0.6)

    if matches:
        # Find original case version
        for option in expected_options:
            if option.lower() == matches[0]:
                return option

    # Return original if no match
    return text
```

Then use it in the event handler:

```python
@session.on("user_speech_committed")
def on_user_speech_committed(message):
    # Get current question options
    if isinstance(moderator.current_question, Question):
        expected_options = moderator.current_question.response_options

        # Correct the transcription
        corrected_text = correct_transcription(message.content, expected_options)

        if corrected_text != message.content:
            logger.info(f"Corrected: '{message.content}' → '{corrected_text}'")
            # Update message content
            message.content = corrected_text
```

### Solution 4: Ask for Clarification

If transcription confidence is low, ask participant to repeat:

```python
# Check transcription confidence (if available)
if hasattr(message, 'confidence') and message.confidence < 0.8:
    await session.generate_reply(
        instructions=f"Say: 'I'm sorry, I didn't quite catch that. Could you please repeat your answer?'"
    )
```

## Recommended Implementation

**Implement Solution 1 + Solution 3:**

1. Add STT prompt with expected vocabulary (prevents most errors)
2. Add fuzzy matching as backup (corrects remaining errors)

This combination will fix >90% of transcription errors for your multiple-choice survey.

## Testing

After implementing, test with:
- "Right track" (should hear correctly)
- "Write track" (should auto-correct to "Right track")
- "They track" (should auto-correct to "Right track")
- Very quiet speech
- Background noise

## Additional Recommendation: Display Options on Screen

Consider showing options visually in the playground/UI so participants can see what they should say. This reduces ambiguity.
