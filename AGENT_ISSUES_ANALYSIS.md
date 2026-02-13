# AI Moderator Agent - Critical Issues Analysis

**Analysis Date:** November 3, 2025
**Session Analyzed:** Latest session from logs (07:00-07:04)
**Analyst:** AI Code Review System

---

## Executive Summary

The AI moderator agent has **three critical issues** that create an infinite loop where it repeatedly asks the same participant the same question without progressing through the survey. This document provides detailed root cause analysis with evidence from session transcripts and code examination.

---

## Issue #1: Agent Asking Same Question Repeatedly (INFINITE LOOP) ⚠️

### Problem Description
The agent gets stuck in a loop where it:
1. Asks participant "identity-X81x" the first question
2. Receives a response ("Base tack")
3. Says "Thank you for your response. Next participant..."
4. Says "Let's move to the next participant."
5. **Immediately asks the SAME participant the SAME question again**
6. Loop repeats indefinitely

### Evidence from Transcript

```
07:02:51 - Moving to question #1
07:02:51 - Selected participant: identity-X81x for question 1 (0/1 have answered so far)
07:02:52 - Agent: "identity-X81x, Do you think that Orange County is on the right track..."
[User responds: "Base tack"]
07:03:17 - Agent: "Thank you for your response. Next participant..."
07:03:17 - Agent: "Let's move to the next participant."
07:03:23 - Selected participant: identity-X81x for question 1 (0/1 have answered so far) ← STILL 0/1!
07:03:23 - Agent asks SAME question again to SAME participant
[User responds: "Braithtrap."]
07:03:44 - Agent: "Thank you for your response. Next participant..."
07:03:45 - Agent: "Let's move to the next participant."
07:03:50 - Selected participant: identity-X81x for question 1 (0/1 have answered so far) ← STILL 0/1!
[LOOP CONTINUES FOREVER...]
```

### Root Cause: RACE CONDITION ⚡

**Location:** `src/moderator_agent.py:903-909`

```python
# When user finishes speaking (on_user_speech_committed event):
if moderator.participant_manager and moderator.current_question_num > 0:
    moderator.participant_manager.update_speaking_time(participant_id, duration)
    moderator.participant_manager.mark_participant_answered(
        participant_id, moderator.current_question_num
    )
    # ⚠️ RACE CONDITION HERE! ⚠️
    asyncio.create_task(moderator.move_to_next_participant())
```

### Why This Creates an Infinite Loop

**The Timeline:**

```
Time 0ms:  User finishes speaking "Base tack"
Time 1ms:  on_user_speech_committed() event triggered
Time 2ms:  mark_participant_answered(identity-X81x, question=1) called
Time 3ms:  asyncio.create_task(move_to_next_participant()) called
           ↓ This returns IMMEDIATELY without waiting!
Time 4ms:  move_to_next_participant() starts running IN PARALLEL
Time 5ms:  move_to_next_participant() calls select_next_participant(question=1)
Time 5ms:  select_next_participant() checks: "Who has answered question 1?"
Time 5ms:  asked_participants[1] = [] ← EMPTY! Marking not finished yet!
Time 6ms:  Logic: "No one answered yet, so select identity-X81x again"
Time 7ms:  mark_participant_answered() finishes executing
Time 8ms:  asked_participants[1] = [identity-X81x] ← TOO LATE!
Time 9ms:  Agent asks identity-X81x the SAME question again
```

**Key Problem:** `asyncio.create_task()` starts the task **asynchronously** (in parallel) and returns immediately. It does NOT wait for the task to finish. So `move_to_next_participant()` starts running BEFORE the `mark_participant_answered()` call has taken effect.

### Detailed Code Flow

```python
# moderator_agent.py:543 (inside move_to_next_participant)
participant = self.participant_manager.select_next_participant(
    self.current_question_num  # question_num = 1
)

# participant_manager.py:73-74 (inside select_next_participant)
answered_this_question = self.asked_participants[question_num]  # Gets who answered question 1
available = [p for p in self.participants if p not in answered_this_question]

# If marking happened: answered_this_question = [identity-X81x]
#                      available = [] → returns None → moves to next question ✓

# If marking NOT done yet: answered_this_question = []
#                          available = [identity-X81x] → selects identity-X81x again ✗
```

### Technical Explanation

The issue is a **classic async race condition**. In Python's asyncio:

- `asyncio.create_task(coroutine())` - Schedules the coroutine to run, returns immediately
- `await coroutine()` - Waits for the coroutine to complete before continuing

The code uses `create_task()` when it should use `await`, causing the participant selection to happen before the marking operation completes.

---

## Issue #2: "Let's Move to the Next Participant" with Only One Participant 👥

### Problem Description
The agent says "Let's move to the next participant" even when there is only ONE participant in the session. This is confusing and nonsensical.

### Evidence from Transcript
```
Participants: 1 total, 0 answered, 1 remaining
Agent: "Thank you. Let's hear from the next participant."
```

When there's only one participant, saying "next participant" doesn't make sense.

### Root Cause

**Location:** `src/moderator_agent.py:564-566`

```python
# Say thank you and indicate moving to next participant
await self.agent_session.generate_reply(
    instructions="Say ONLY: 'Thank you. Let's hear from the next participant.' Then STOP."
)
```

**The Problem:** No check for participant count. This message is spoken regardless of whether there are 1, 2, or 10 participants.

### What the Code Should Check

```python
# Variables available at this point (lines 522-525):
total_participants = len(self.participant_manager.participants)
answered_count = len(self.participant_manager.asked_participants.get(self.current_question_num, []))
remaining = total_participants - answered_count

# Should have conditional logic:
if remaining > 1:
    # Multiple participants remaining
    message = "Thank you. Let's hear from the next participant."
elif total_participants == 1:
    # Only one participant in the entire session
    message = "Thank you."
else:
    # Last participant for this question
    message = "Thank you. Moving to the next question."
```

---

## Issue #3: Asking Same Question Immediately Without Pause ⏱️

### Problem Description
After saying "Let's move to the next participant," the agent immediately asks the question again with barely any pause. This feels robotic and rushed.

### Evidence from Transcript
```
07:03:17 - Agent: "Thank you for your response. Next participant..."
07:03:17 - Agent: "Let's move to the next participant."
07:03:23 - Agent immediately asks: "identity-X81x, Do you think..."
```

Only **6 seconds** total between receiving response and asking next question. No natural conversational breathing room.

### Root Cause

**Location:** `src/moderator_agent.py:567`

```python
# Say thank you and indicate moving to next participant
await self.agent_session.generate_reply(
    instructions="Say ONLY: 'Thank you. Let's hear from the next participant.' Then STOP."
)
await asyncio.sleep(1.5)  # ← TOO SHORT - only 1.5 seconds
```

**The Problem:** 1.5 seconds is not enough time for:
- The acknowledgment to finish playing (TTS takes time)
- A natural conversational pause
- The participant to mentally prepare for the next person's turn

### User Experience Impact

```
Human conversation flow:
"Thank you, John." [pause] [2-3 seconds] "Sarah, what do you think?"
                   ^^^^^^^^^ Natural breathing room

Current agent flow:
"Thank you." [1.5 sec] "identity-X81x, what do you think?"
             ^^^^^^^^^ Too rushed, robotic
```

**Better Timing:**
```python
await asyncio.sleep(2.5)  # More natural 2.5-3 second pause
```

---

## Additional Critical Issues Discovered

### Issue #4: Duplicate Processing / Double Trigger 🔄

**Evidence from Logs:**
```
2025-11-03 07:02:51,174 - __mp_main__ - INFO - Starting question-based survey
2025-11-03 07:02:51,174 - __mp_main__ - INFO - Starting question-based survey  ← DUPLICATE!
2025-11-03 07:02:51,175 - src.moderator_agent - INFO - Moving to question #1
2025-11-03 07:02:51,175 - src.participant_manager - INFO - Selected participant: identity-X81x
2025-11-03 07:02:51,175 - src.moderator_agent - INFO - Moving to question #1  ← DUPLICATE!
2025-11-03 07:02:51,175 - src.participant_manager - INFO - Selected participant: identity-X81x  ← DUPLICATE!
```

**Analysis:** The survey is being started TWICE simultaneously. Every operation is logged twice at the exact same millisecond (07:02:51,174 and 07:02:51,175).

**Likely Causes:**

1. **Multiple async coroutines** calling `ask_next_question()` simultaneously
2. **Event handler registered twice** in the setup code
3. **Worker process duplication** - `agent.py:182` sets `num_idle_processes=1` but there may be multiple workers

**Location to Investigate:** `agent.py:136-137`

```python
# Start asking questions
if hasattr(moderator, 'ask_next_question'):
    logger.info("Starting question-based survey")
    await moderator.ask_next_question()  # ← Is this being called twice?
```

**Impact:** This compounds the race condition issue. With duplicate processing:
- Participant gets selected twice
- Question asked twice
- Response handling happens twice
- Makes the loop even worse

---

### Issue #5: LLM Temperature Too High 🌡️

**Evidence from Logs:**
```python
# From actual API calls in logs:
'json_data': {
    'messages': [...],
    'model': 'gpt-4o-mini',
    'stream': True,
    'temperature': 0.7  # ← Too high for structured survey moderation
}
```

**Expected vs. Actual:**

```python
# Code at moderator_agent.py:743
llm=openai.LLM(
    model=llm_model,
    temperature=0.1,  # Configured for deterministic responses
),

# But config overrides this at agent.py:86
temperature=config.temperature  # Likely set to 0.7 in .env.local
```

**Problem:** Temperature of 0.7 allows creative, varied responses. For a survey moderator that needs to follow strict rules and repeat questions verbatim, this causes:
- Unpredictable behavior
- Paraphrasing questions instead of reading them exactly
- Adding extra commentary
- Not following instructions precisely

**Impact on Survey Quality:**
- Questions may be asked differently each time
- Response options might be stated inconsistently
- Professional survey methodology requires consistent question delivery

**Should Be:** `temperature=0.1` or lower for consistent, deterministic, rule-following responses.

---

## Summary: Why Agent Loops Forever

**The Perfect Storm:**

1. **User responds** to question #1
2. **Event handler triggers** both `mark_participant_answered()` and `move_to_next_participant()`
3. **Race condition:** `move_to_next_participant()` runs BEFORE marking completes
4. **Selection logic** sees `asked_participants[1] = []` (empty list)
5. **Same participant selected** because marking hasn't taken effect yet
6. **Agent asks same question** to same participant
7. **Loop repeats** infinitely because participant is never properly marked as "answered"

### Visual Representation

```
┌─────────────────────────────────────┐
│ User responds "Base tack"           │
└──────────────┬──────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────────────┐
│ on_user_speech_committed() event triggered          │
└──────────────┬──────────────────────────────────────┘
               │
               ├────────────────────────┐
               │                        │
               ▼                        ▼
┌──────────────────────────┐   ┌─────────────────────────────┐
│ mark_participant_answered│   │ asyncio.create_task(        │
│ (participant, question=1)│   │   move_to_next_participant) │
│                          │   │                             │
│ Updates dictionary:      │   │ Starts running immediately  │
│ asked_participants[1]    │   │ WITHOUT waiting for marking │
│ = [identity-X81x]        │   └────────┬────────────────────┘
│                          │            │
│ [Slow - takes time]      │            │ [Fast - runs in parallel]
└──────────────────────────┘            │
                                        ▼
                           ┌─────────────────────────────────┐
                           │ select_next_participant(q=1)    │
                           │                                 │
                           │ Checks: asked_participants[1]   │
                           │ Result: [] (EMPTY!)             │
                           │                                 │
                           │ Conclusion: "No one answered"   │
                           │ Action: Select identity-X81x    │
                           └────────┬────────────────────────┘
                                    │
                                    ▼
                           ┌─────────────────────────────────┐
                           │ Ask SAME question to            │
                           │ SAME participant again          │
                           └────────┬────────────────────────┘
                                    │
                                    ▼
                           ┌─────────────────────────────────┐
                           │ 🔄 INFINITE LOOP! 🔄            │
                           └─────────────────────────────────┘
```

### State Diagram

```
                    ┌─────────────────┐
                    │  Initial State  │
                    │  asked=[0]      │
                    └────────┬────────┘
                             │
                             ▼
                    ┌─────────────────┐
                    │ Ask Question #1 │
                    │ to identity-X81x│
                    └────────┬────────┘
                             │
                             ▼
                    ┌─────────────────┐
                    │ User Responds   │
                    └────────┬────────┘
                             │
                ┌────────────┴────────────┐
                │                         │
                ▼                         ▼
    ┌──────────────────┐      ┌──────────────────┐
    │ SHOULD happen:   │      │ ACTUALLY happens:│
    │                  │      │                  │
    │ 1. Mark answered │      │ 1. Start marking │
    │ 2. Wait complete │      │ 2. Start moving  │
    │ 3. Move to next  │      │    (in parallel!)│
    │ 4. Check: Who    │      │ 3. Check: Who    │
    │    answered?     │      │    answered?     │
    │ 5. Result: X81x  │      │ 4. Result: []    │
    │    answered!     │      │    (not done yet)│
    │ 6. Select NEW    │      │ 5. Select X81x   │
    │    participant   │      │    AGAIN         │
    │ 7. Move to Q2    │      │ 6. Ask Q1 again  │
    │    ✓             │      │    ✗ LOOP!       │
    └──────────────────┘      └──────────────────┘
```

---

## Detailed Fix Recommendations (Priority Order)

### 🚨 Priority 1: Fix the Race Condition (CRITICAL)

**This fix will resolve the infinite loop entirely.**

**Option A - Use await instead of create_task (RECOMMENDED):**

```python
# File: src/moderator_agent.py
# Line: 909

# OLD (BROKEN):
asyncio.create_task(moderator.move_to_next_participant())

# NEW (FIXED):
await moderator.move_to_next_participant()
```

**Why this works:** Using `await` forces the code to wait for `move_to_next_participant()` to complete before returning from the event handler. This ensures marking is done before selection happens.

**Option B - Add delay to ensure marking completes:**

```python
# File: src/moderator_agent.py
# Lines: 905-909

# OLD (BROKEN):
moderator.participant_manager.mark_participant_answered(
    participant_id, moderator.current_question_num
)
asyncio.create_task(moderator.move_to_next_participant())

# NEW (FIXED):
moderator.participant_manager.mark_participant_answered(
    participant_id, moderator.current_question_num
)
await asyncio.sleep(0.1)  # Ensure state update propagates
asyncio.create_task(moderator.move_to_next_participant())
```

**Why this works:** The small delay gives the marking operation time to complete before the selection starts.

**Recommendation:** Use **Option A** (await) - it's cleaner, more reliable, and doesn't rely on timing assumptions.

---

### ⚠️ Priority 2: Fix "Next Participant" Message (HIGH)

**This fix will improve user experience and prevent confusion.**

**Location:** `src/moderator_agent.py:559-567`

```python
# CURRENT CODE (lines 559-567):
# FIXED: There ARE more participants - acknowledge current and move to next
logger.info(f"Moving to next participant '{participant}' for same question")
logger.info(f"{remaining} participant(s) remaining for question #{self.current_question_num}")

# Say thank you and indicate moving to next participant
await self.agent_session.generate_reply(
    instructions="Say ONLY: 'Thank you. Let's hear from the next participant.' Then STOP."
)
await asyncio.sleep(1.5)
```

**REPLACE WITH:**

```python
# NEW CODE (with participant count check):
logger.info(f"Moving to next participant '{participant}' for same question")
logger.info(f"{remaining} participant(s) remaining for question #{self.current_question_num}")

# Calculate participant counts
total_participants = len(self.participant_manager.participants)
answered_count = len(self.participant_manager.asked_participants.get(self.current_question_num, []))
remaining = total_participants - answered_count

# Acknowledge response with appropriate transition message
if total_participants == 1:
    # Only one participant in entire session - don't mention "next participant"
    await self.agent_session.generate_reply(
        instructions="Say ONLY: 'Thank you.' Then STOP."
    )
elif remaining > 1:
    # Multiple participants still need to answer
    await self.agent_session.generate_reply(
        instructions="Say ONLY: 'Thank you. Let's hear from the next participant.' Then STOP."
    )
else:
    # This was the last participant for this question
    await self.agent_session.generate_reply(
        instructions="Say ONLY: 'Thank you. Moving to the next question.' Then STOP."
    )

await asyncio.sleep(2.5)  # Also increased delay (see Priority 3)
```

**Impact:** Agent will now:
- Say just "Thank you" when there's only one participant
- Say "next participant" only when there are actually multiple participants
- Announce "next question" when moving between questions

---

### 📊 Priority 3: Increase Transition Delay (MEDIUM)

**This fix will make conversations feel more natural.**

**Location:** `src/moderator_agent.py:567`

```python
# OLD:
await asyncio.sleep(1.5)  # Too short

# NEW:
await asyncio.sleep(2.5)  # More natural pause
```

**Also update at line 537:**

```python
# OLD:
await asyncio.sleep(0.5)  # Faster response flow

# NEW:
await asyncio.sleep(1.5)  # More natural for question transitions
```

**Rationale:**
- TTS (text-to-speech) takes ~1-2 seconds to play
- Human conversation has natural pauses of 1-3 seconds
- Total time should be 2.5-3 seconds for professional feel

---

### 🔧 Priority 4: Lower LLM Temperature (MEDIUM)

**This fix will make agent follow instructions more precisely.**

**Location:** `.env.local` or `config/agent_config.py`

```bash
# In .env.local file:
# OLD:
TEMPERATURE=0.7

# NEW:
TEMPERATURE=0.1
```

**Or in config/agent_config.py if hardcoded:**

```python
# OLD:
temperature: float = 0.7

# NEW:
temperature: float = 0.1
```

**Impact:**
- Agent will follow instructions more precisely
- Questions will be read verbatim without paraphrasing
- More consistent, professional survey delivery
- Less creative "improvisation" that deviates from script

---

### 🔍 Priority 5: Investigate Double Triggering (LOW)

**This fix will improve system stability.**

**Location:** `agent.py:136-137`

Add logging to track if this is being called multiple times:

```python
# OLD:
if hasattr(moderator, 'ask_next_question'):
    logger.info("Starting question-based survey")
    await moderator.ask_next_question()

# NEW (with tracking):
if hasattr(moderator, 'ask_next_question'):
    import traceback
    logger.info("Starting question-based survey")
    logger.debug(f"Call stack:\n{''.join(traceback.format_stack())}")
    await moderator.ask_next_question()
```

**Investigation steps:**
1. Check if worker processes are spawning duplicates
2. Verify event handlers aren't registered multiple times
3. Look for async race conditions in entrypoint
4. Review LiveKit agent framework documentation

---

## Complete Fix Implementation

### Step-by-Step Implementation Guide

#### Step 1: Fix Race Condition

Edit `src/moderator_agent.py` at line 909:

```python
# Find this section around line 902-910:
if moderator.participant_manager and moderator.current_question_num > 0:
    moderator.participant_manager.update_speaking_time(participant_id, duration)
    moderator.participant_manager.mark_participant_answered(
        participant_id, moderator.current_question_num
    )
    # Move to next participant (don't await in event handler)
    asyncio.create_task(moderator.move_to_next_participant())  # ← CHANGE THIS LINE
```

Change to:

```python
if moderator.participant_manager and moderator.current_question_num > 0:
    moderator.participant_manager.update_speaking_time(participant_id, duration)
    moderator.participant_manager.mark_participant_answered(
        participant_id, moderator.current_question_num
    )
    # Move to next participant (FIXED: await to ensure marking completes)
    await moderator.move_to_next_participant()  # ← CHANGED: removed create_task
```

#### Step 2: Fix Participant Count Message

Edit `src/moderator_agent.py` around lines 559-567:

```python
# Replace the entire section from line 559-567 with:

# FIXED: There ARE more participants - acknowledge current and move to next
logger.info(f"Moving to next participant '{participant}' for same question")
logger.info(f"{remaining} participant(s) remaining for question #{self.current_question_num}")

# Calculate participant counts for intelligent messaging
total_participants = len(self.participant_manager.participants)

# Acknowledge response with context-aware transition message
if total_participants == 1:
    # Only one participant in entire session
    await self.agent_session.generate_reply(
        instructions="Say ONLY: 'Thank you.' Then STOP."
    )
elif remaining > 1:
    # Multiple participants still need to answer this question
    await self.agent_session.generate_reply(
        instructions="Say ONLY: 'Thank you. Let's hear from the next participant.' Then STOP."
    )
else:
    # Last participant for this question (shouldn't happen due to earlier check, but safety)
    await self.agent_session.generate_reply(
        instructions="Say ONLY: 'Thank you.' Then STOP."
    )

await asyncio.sleep(2.5)  # Natural pause before next question (increased from 1.5)
```

#### Step 3: Increase Delays

Edit `src/moderator_agent.py`:

```python
# Line 537: Change from 0.5 to 1.5
await asyncio.sleep(1.5)  # Changed from 0.5 - more natural for transitions

# Line 567: Already changed in Step 2 above to 2.5
```

#### Step 4: Lower Temperature

Edit `.env.local` file:

```bash
# Find the TEMPERATURE setting and change it:
TEMPERATURE=0.1
```

Or if you don't have this setting, add it:

```bash
# At the end of .env.local:
TEMPERATURE=0.1
```

---

## Testing Checklist

After applying fixes, perform these tests:

### Test 1: Single Participant Scenario
- [ ] Join with ONE participant
- [ ] Agent asks first question
- [ ] Respond to question
- [ ] Verify agent says "Thank you" WITHOUT "next participant"
- [ ] Verify agent moves to question #2
- [ ] Complete all questions - no loops

### Test 2: Multiple Participants Scenario
- [ ] Join with TWO or more participants
- [ ] Agent asks first question to participant A
- [ ] Participant A responds
- [ ] Verify agent says "Let's hear from the next participant"
- [ ] Verify agent asks SAME question to participant B
- [ ] Participant B responds
- [ ] Verify agent moves to question #2
- [ ] Verify each participant asked once per question

### Test 3: Question Progression
- [ ] Complete question #1 with all participants
- [ ] Verify logs show correct count (e.g., "2/2 have answered")
- [ ] Verify agent moves to question #2
- [ ] Verify question #2 asked to all participants
- [ ] Continue through all 7 questions
- [ ] Verify no infinite loops at any point

### Test 4: Conversation Flow
- [ ] Time the pauses between:
  - Response → "Thank you" (should be immediate)
  - "Thank you" → Next question (should be ~2.5 seconds)
  - Question → Wait for response (should be patient, 10+ seconds)
- [ ] Verify flow feels natural, not rushed

### Test 5: Question Consistency
- [ ] Verify questions read verbatim from JSON
- [ ] Verify ALL response options are stated
- [ ] Verify no paraphrasing or improvisation
- [ ] Verify participant name said once per question

### Test 6: No Duplicate Processing
- [ ] Check logs for duplicate "Starting question-based survey"
- [ ] Check logs for duplicate "Selected participant" at same timestamp
- [ ] Verify each operation logged only once

### Test 7: Edge Cases
- [ ] Participant disconnects mid-survey
- [ ] Participant doesn't respond (wait 30 seconds)
- [ ] Participant joins late
- [ ] All participants answered current question

### Expected Log Output (After Fixes)

```
07:02:51 - Starting question-based survey
07:02:51 - Moving to question #1
07:02:51 - Selected participant: identity-X81x for question 1 (0/1 have answered)
07:02:52 - Agent: "identity-X81x, Do you think..."
[User responds: "Right track"]
07:03:10 - Marked identity-X81x as answered for question 1 (1/1 have answered)
07:03:10 - All participants answered current question, moving to next question
07:03:11 - Agent: "Thank you."
07:03:13 - Moving to question #2
07:03:14 - Selected participant: identity-X81x for question 2 (0/1 have answered)
07:03:15 - Agent: "identity-X81x, [Question 2 text]..."
```

Key indicators of success:
- ✓ Only ONE "Starting question-based survey"
- ✓ Count shows "1/1 have answered" after first response
- ✓ "All participants answered" message appears
- ✓ Moves to question #2
- ✓ No repeated questions to same participant

---

## Appendix: Understanding Async Race Conditions

### What is a Race Condition?

A race condition occurs when the behavior of software depends on the relative timing of events, such as the order in which threads or async tasks execute.

### The Problem with `asyncio.create_task()`

```python
# This code has a race condition:
mark_participant_answered(participant, question)  # Operation A
asyncio.create_task(move_to_next_participant())    # Operation B

# What happens:
# 1. Operation A starts executing
# 2. Operation B is SCHEDULED (added to event loop)
# 3. Operation B STARTS executing (possibly before A finishes!)
# 4. Operation B reads state that Operation A is still updating
# 5. Result: Inconsistent state
```

### The Fix with `await`

```python
# This code is safe:
mark_participant_answered(participant, question)  # Operation A
await move_to_next_participant()                   # Operation B

# What happens:
# 1. Operation A starts and completes
# 2. Operation B starts AFTER A completes
# 3. Operation B reads consistent state
# 4. Result: Correct behavior
```

### Why Event Handlers Matter

Event handlers in async frameworks like LiveKit are special:

```python
@session.on("user_speech_committed")
def on_user_speech_committed(message):
    # This runs in the event loop
    # Any async operations need careful handling

    # WRONG (race condition):
    mark_answered()
    asyncio.create_task(move_next())

    # RIGHT (sequential):
    mark_answered()
    await move_next()
```

---

## Appendix: File Locations Reference

| Issue | File | Line Numbers |
|-------|------|--------------|
| Race condition | `src/moderator_agent.py` | 909 |
| Participant count message | `src/moderator_agent.py` | 564-566 |
| Transition delay | `src/moderator_agent.py` | 537, 567 |
| LLM temperature | `.env.local` or `config/agent_config.py` | N/A (config) |
| Double triggering | `agent.py` | 136-137 |
| Event handler | `src/moderator_agent.py` | 833-910 |
| Participant selection | `src/participant_manager.py` | 50-88 |

---

## Contact & Support

**Document Version:** 1.0
**Last Updated:** November 3, 2025
**Status:** Ready for Implementation

**Next Steps:**
1. Review this analysis with development team
2. Implement fixes in priority order
3. Test thoroughly using checklist
4. Monitor logs for any remaining issues
5. Update documentation with lessons learned

---

**End of Report**
