# AI Moderator Agent - Root Cause Analysis & Fixes

**Date:** November 3, 2025
**Critical Issue:** Infinite Loop - Agent repeats same question to same participant

---

## THE TRUE ROOT CAUSE 🎯

After deep analysis of logs and code, the root cause has been identified:

### **Event Handlers Are NEVER Firing**

The agent uses **manual turn detection** (line 750 in `src/moderator_agent.py`):

```python
turn_detection="manual",  # Disable automatic responses
```

**With manual turn detection:**
- The LiveKit framework does NOT automatically track user speech
- Event handlers `user_started_speaking` and `user_speech_committed` are NEVER triggered
- User speech IS captured (via STT) and added to LLM conversation
- But NO events fire to notify the code that user has spoken

**Evidence from Logs:**

```bash
# These log messages NEVER appear (from lines 788 and 854):
"User started speaking: {participant.identity}"
"User finished speaking: {participant_id}"

# This log message NEVER appears (from line 125 in participant_manager.py):
"Marked {participant_identity} as answered for question {question_num}"
```

###What DOES Happen:

1. Agent asks question via `generate_reply()`
2. User speaks → STT captures it
3. User speech added to LLM conversation as `{'role': 'user', 'content': 'response'}`
4. **NO EVENT FIRES** (because turn detection is manual)
5. Code waiting for `on_user_speech_committed` to call `move_to_next_participant()`
6. Event NEVER fires, so participant NEVER marked as answered
7. LLM autonomously continues conversation based on context
8. LLM sees pattern and asks same question again
9. Infinite loop

---

## Why Manual Turn Detection?

Looking at the comment (line 738):
```python
# CRITICAL: Manual turn detection prevents automatic responses and hallucinations
# We explicitly control all agent speech via generate_reply() calls
```

The manual mode was chosen to prevent the agent from speaking automatically. But this breaks the event-based participant tracking!

---

## The Fix Strategy

We have **two options**:

### Option A: Switch to Server VAD Turn Detection (RECOMMENDED)

Change to automatic turn detection which fires the events we need:

```python
# Line 750 in moderator_agent.py
turn_detection="server_vad",  # Enable automatic turn detection for events
```

**Pros:**
- Events will fire correctly
- Participant tracking will work
- Existing event handler logic works as designed

**Cons:**
- Agent might try to speak automatically (we prevent this with good instructions)
- Need to ensure LLM instructions prevent hallucinations

### Option B: Keep Manual Mode But Add Explicit Tracking

Keep manual mode and explicitly track user responses via a different mechanism:

**Pros:**
- Maintains strict control over agent speech
- No risk of automatic responses

**Cons:**
- Requires rewriting the entire participant tracking system
- More complex solution
- Need to manually detect when user speaks

---

## RECOMMENDED FIX: Option A (Switch to Server VAD)

This is the cleanest solution that works with the existing architecture.

### Step 1: Change Turn Detection Mode

**File:** `src/moderator_agent.py`
**Line:** 750

```python
# OLD:
turn_detection="manual",  # Disable automatic responses - we control when agent speaks

# NEW:
turn_detection="server_vad",  # Enable turn detection for event handling
```

### Step 2: Strengthen LLM Instructions to Prevent Auto-Responses

The LLM might try to respond automatically when it sees user input. We need VERY strong instructions to prevent this.

**File:** `config/agent_config.py` (or wherever moderator instructions are defined)

Add to the base instructions:

```python
"""
CRITICAL BEHAVIORAL RULES:
1. ONLY speak when given EXPLICIT instructions via system messages
2. NEVER respond automatically to user messages
3. WAIT for explicit instructions before generating any response
4. If you receive a user message without instructions, DO NOTHING
5. You are not in a free-form conversation - you follow a strict script

These rules override your normal conversational behavior.
"""
```

### Step 3: Test Event Handlers Are Firing

After making the change, verify in logs:

```bash
# Should see these messages:
"User started speaking: identity-X81x"
"User finished speaking: identity-X81x, duration: X.Xs"
"Marked identity-X81x as answered for question 1 (1/1 have answered)"
```

---

## Additional Fixes (Priority Order)

###Fix #2: Participant Count Messaging

**File:** `src/moderator_agent.py`
**Lines:** 559-567

```python
# CURRENT:
# Say thank you and indicate moving to next participant
await self.agent_session.generate_reply(
    instructions="Say ONLY: 'Thank you. Let's hear from the next participant.' Then STOP."
)
await asyncio.sleep(1.5)

# FIXED:
# Calculate participant counts
total_participants = len(self.participant_manager.participants)
answered_count = len(self.participant_manager.asked_participants.get(self.current_question_num, []))
remaining = total_participants - answered_count

# Context-aware messaging
if total_participants == 1:
    # Only one participant - don't mention "next participant"
    await self.agent_session.generate_reply(
        instructions="Say ONLY: 'Thank you.' Then STOP."
    )
elif remaining > 1:
    # Multiple participants remaining
    await self.agent_session.generate_reply(
        instructions="Say ONLY: 'Thank you. Let's hear from the next participant.' Then STOP."
    )
else:
    # Last participant for this question
    await self.agent_session.generate_reply(
        instructions="Say ONLY: 'Thank you.' Then STOP."
    )

await asyncio.sleep(2.5)  # Increased from 1.5s for better flow
```

### Fix #3: Increase Conversation Delays

**File:** `src/moderator_agent.py`

**Line 537:**
```python
# OLD:
await asyncio.sleep(0.5)  # Faster response flow

# NEW:
await asyncio.sleep(1.5)  # More natural pause between questions
```

**Line 567** (if not already changed in Fix #2):
```python
# OLD:
await asyncio.sleep(1.5)

# NEW:
await asyncio.sleep(2.5)  # More natural conversation flow
```

### Fix #4: Lower LLM Temperature

**File:** `.env.local`

```bash
# OLD:
TEMPERATURE=0.7

# NEW:
TEMPERATURE=0.1
```

This makes the LLM more deterministic and rule-following.

---

## Alternative Fix: Option B (Manual Mode with Explicit Tracking)

If you must keep `turn_detection="manual"`, you need to replace the event-based system with explicit tracking.

### Implementation Approach:

1. **Remove dependency on events** - Don't rely on `user_speech_committed`

2. **Explicitly track responses in the LLM loop:**

```python
async def ask_question_and_wait_for_response(self, participant, question_num):
    """Ask question and explicitly wait for response."""

    # Ask the question
    await self.agent_session.generate_reply(
        instructions=f"{participant}, {self.current_question}"
    )

    # Wait for user speech (you'll need to implement this detection)
    response = await self.wait_for_user_speech(timeout=30)

    if response:
        # Mark participant as answered
        self.participant_manager.mark_participant_answered(
            participant, question_num
        )

        # Move to next
        await self.move_to_next_participant()
    else:
        # Timeout - no response
        await self.handle_no_response(participant)
```

3. **Implement `wait_for_user_speech()` method:**

This is the challenging part with manual turn detection. You'd need to:
- Monitor the STT stream directly
- Detect when speech starts and ends
- Extract the transcribed text
- Return it to the caller

**This is significantly more complex than Option A.**

---

## Testing Plan

After implementing Option A (recommended), test:

### Test 1: Verify Events Fire
```bash
# Check logs for:
- "User started speaking"
- "User finished speaking"
- "Marked ... as answered"
```

### Test 2: Single Participant Flow
- Join with 1 participant
- Answer question 1
- Verify: Agent says "Thank you" (NOT "next participant")
- Verify: Moves to question 2
- Complete all 7 questions with no loops

### Test 3: Multiple Participants Flow
- Join with 2+ participants
- Answer question 1 as participant A
- Verify: Agent says "Let's hear from the next participant"
- Answer question 1 as participant B
- Verify: Moves to question 2
- No infinite loops

### Test 4: Verify Counts
Check logs show correct progression:
```
Selected participant: identity-X81x for question 1 (0/1 have answered)
Marked identity-X81x as answered for question 1 (1/1 have answered)
All participants answered current question, moving to next question
Moving to question #2
Selected participant: identity-X81x for question 2 (0/1 have answered)
```

---

## Why This Fix Works

**Current broken flow:**
```
Ask Question → User Speaks → [NO EVENT] → LLM auto-responds → Loop
```

**Fixed flow with server_vad:**
```
Ask Question → User Speaks → Event Fires → Mark Answered → Move to Next → Ask Next Question
```

The events are the critical missing piece that trigger participant tracking!

---

## Risk Assessment

### Risks of Option A (server_vad):

**Risk:** Agent might respond automatically to user speech

**Mitigation:**
1. Strong LLM instructions to ONLY speak when explicitly told
2. Low temperature (0.1) for rule-following behavior
3. System messages clearly state "WAIT for instructions"
4. Test thoroughly to ensure no automatic responses

**Risk:** Turn detection might interrupt user mid-speech

**Mitigation:**
- VAD (Voice Activity Detection) is designed to handle this
- Silero VAD is used (line 749) which is robust
- Max turn duration settings (20s) provide safety nets

### Risks of Option B (manual + explicit):

**Risk:** Complex implementation with many edge cases

**Impact:**
- Higher development time
- More bugs to fix
- Harder to maintain

---

## Implementation Priority

1. **CRITICAL (Do First):** Fix #1 - Change to server_vad turn detection
2. **HIGH:** Fix #2 - Participant count messaging
3. **MEDIUM:** Fix #3 - Conversation flow delays
4. **MEDIUM:** Fix #4 - Lower temperature
5. **LOW:** Add monitoring/logging for turn detection events

---

## Code Changes Summary

### File: `src/moderator_agent.py`

**Line 750:**
```python
turn_detection="server_vad",  # Changed from "manual"
```

**Lines 559-567:**
```python
# Add participant count check (see Fix #2 above)
```

**Line 537:**
```python
await asyncio.sleep(1.5)  # Changed from 0.5
```

**Line 567:**
```python
await asyncio.sleep(2.5)  # Changed from 1.5
```

### File: `config/agent_config.py`

Add strong instructions to prevent auto-responses (see Step 2)

### File: `.env.local`

```bash
TEMPERATURE=0.1  # Changed from 0.7
```

---

## Expected Behavior After Fix

### Correct Question Flow:

```
07:02:51 - Moving to question #1
07:02:51 - Selected participant: identity-X81x for question 1 (0/1 have answered)
07:02:52 - Agent: "identity-X81x, Do you think..."
[User responds: "Right track"]
07:03:05 - User started speaking: identity-X81x
07:03:07 - User finished speaking: identity-X81x, duration: 2.1s
07:03:07 - Marked identity-X81x as answered for question 1 (1/1 have answered)
07:03:07 - After response: 1/1 participants have answered question #1
07:03:07 - All participants answered current question, moving to next question
07:03:08 - Agent: "Thank you."
07:03:10 - Moving to question #2
07:03:11 - Selected participant: identity-X81x for question 2 (0/1 have answered)
07:03:12 - Agent: "identity-X81x, [Question 2]..."
```

Key indicators:
- ✅ User start/finish events fire
- ✅ Participant marked as answered
- ✅ Count shows 1/1
- ✅ Moves to next question
- ✅ No infinite loop

---

## Conclusion

The root cause was **manual turn detection disabling the event system** that the code depends on to track participant responses.

**The fix is simple:** Change one line from `"manual"` to `"server_vad"`.

Everything else (event handlers, participant tracking, question flow) is already correctly implemented - it just needed the events to actually fire!

---

**Status:** Ready for implementation
**Complexity:** Low (one-line change + testing)
**Risk:** Low (with proper LLM instructions)
**Impact:** Fixes infinite loop completely

---

