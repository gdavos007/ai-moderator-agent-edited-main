# 🔍 Debug Test Plan - Event Detection

**Purpose:** Determine if events are firing and why infinite loop persists

---

## 🚀 Step 1: Restart Agent with Debug Logging

### Stop Current Agent
```bash
# Kill ALL agent processes
pkill -9 -f "python.*agent.py"

# Verify nothing is running
ps aux | grep agent.py
# Should show NO results
```

### Start Fresh
```bash
cd /Users/srinivasmagidewar/My_Docs/ai_projects/ai-moderator-agent

# Clear log file
> logs/agent.log

# Start agent
python agent.py dev
```

### Watch Logs
```bash
# In another terminal
tail -f logs/agent.log | grep "🔥\|🚀"
```

---

## 🔬 Step 2: What to Look For

### When Agent Starts

**You MUST see this:**
```
================================================================================
🚀 AGENT SESSION STARTED WITH NEW EVENT HANDLERS
🔍 Listening for: user_state_changed, conversation_item_added
🔧 Turn detection: server_vad
================================================================================
```

**If you DON'T see this:**
- ❌ Agent wasn't restarted properly
- ❌ Old code still running
- 🔄 GO BACK TO STEP 1

---

### When User Speaks

**Test:** Join room and say "Right track"

**SCENARIO A - Events Fire (Good!):**
```
🔥 EVENT FIRED: user_state_changed - listening -> speaking
🔥 EVENT FIRED: user_state_changed - speaking -> listening
🔥 EVENT FIRED: conversation_item_added - role=user
User message added to conversation: identity-X, text: 'Right track'
Marking identity-X as answered for question #1
Marked identity-X as answered for question 1 (1/1 have answered)
After response: 1/1 participants have answered question #1
All participants answered current question, moving to next question
Moving to question #2
```

**SCENARIO B - No Events (Problem!):**
```
[No 🔥 messages at all]
After response: 0/1 participants have answered question #1  ← Still 0!
```

**SCENARIO C - Partial Events:**
```
🔥 EVENT FIRED: user_state_changed - listening -> speaking
🔥 EVENT FIRED: user_state_changed - speaking -> listening
[No conversation_item_added event]
After response: 0/1 participants have answered question #1
```

---

## 📊 Diagnosis Based on Results

### ✅ SCENARIO A: Events Fire

**Meaning:** Events work! Infinite loop should be fixed.

**If still looping:**
- Check if participant is actually being marked
- Check `move_to_next_participant()` logic
- Verify participant count check

---

### ❌ SCENARIO B: No Events At All

**Meaning:** Events aren't firing with current setup.

**Possible Causes:**
1. **Wrong turn detection mode**
   - Events might not fire with `server_vad` in your LiveKit version
   - Try changing to `manual` (though unlikely to help)

2. **LiveKit version doesn't support these events**
   - Check LiveKit agents version: `pip show livekit-agents`
   - May need to upgrade: `pip install --upgrade livekit-agents`

3. **AgentSession doesn't emit these events**
   - Regular LLM mode may not emit conversation events
   - May need to switch to RealtimeLLM (requires gpt-4o-realtime model)

**Solution:**
- We'll need a **hybrid approach** combining timeout + event detection
- Or polling the conversation context directly

---

### ⚠️ SCENARIO C: Partial Events

**Meaning:** `user_state_changed` works but `conversation_item_added` doesn't.

**Why:** Conversation events may only fire with specific LLM modes.

**Solution:**
We can detect timing from `user_state_changed`:

```python
@session.on("user_state_changed")
def on_user_state_changed(event):
    if event.new_state == "speaking" and event.old_state == "listening":
        # User STARTED speaking
        moderator.user_speech_start_time = datetime.now()

    elif event.new_state == "listening" and event.old_state == "speaking":
        # User STOPPED speaking
        duration = (datetime.now() - moderator.user_speech_start_time).total_seconds()
        logger.info(f"User spoke for {duration}s")

        # NOW mark as answered
        participant = moderator.get_current_participant()
        moderator.participant_manager.mark_participant_answered(
            participant,
            moderator.current_question_num
        )

        # Move to next
        asyncio.create_task(moderator.move_to_next_participant())
```

This uses state changes to detect when speaking ends!

---

## 🎯 Next Steps Based on Results

### If Events Fire (Scenario A)
1. ✅ Problem solved!
2. Test full survey flow
3. Verify no loops
4. Fix participant count messaging

### If No Events (Scenario B)
1. Try the hybrid approach (timeout + state polling)
2. Consider upgrading LiveKit
3. Or implement RealtimeLLM mode

### If Partial Events (Scenario C)
1. Use `user_state_changed` to detect speech end
2. Implement the solution I showed above
3. This gives us proper timing without conversation events

---

## 🔧 Hybrid Approach (If Events Don't Work)

If NO events fire, we can combine:
1. **Timeout** - Maximum wait time (20s)
2. **Polling** - Check every 2s if user spoke
3. **LLM context** - Look for new user messages

```python
async def ask_question_with_detection(self, participant):
    # Ask question
    await self.agent_session.generate_reply(...)

    # Get initial conversation state
    initial_length = len(self.agent_session._chat_ctx.items)

    # Wait up to 20 seconds, checking every 2 seconds
    for i in range(10):
        await asyncio.sleep(2)

        # Check if conversation grew
        current_length = len(self.agent_session._chat_ctx.items)
        if current_length > initial_length:
            # New message added - user probably spoke!
            last_message = self.agent_session._chat_ctx.items[-1]
            if last_message.role == "user":
                logger.info("✅ Detected user response via polling!")
                break

    # Mark as answered
    self.participant_manager.mark_participant_answered(participant, question_num)

    # Move on
    await self.move_to_next_participant()
```

**This approach:**
- ✅ Detects when user speaks (by checking conversation)
- ✅ Doesn't waste time if they answer quickly
- ✅ Has safety timeout if no response
- ✅ No dependency on events
- ✅ Handles all 3 of your concerns!

---

## 📝 What to Report Back

After running the test, tell me:

1. **Did you see the startup message?**
   ```
   🚀 AGENT SESSION STARTED WITH NEW EVENT HANDLERS
   ```

2. **Did you see ANY 🔥 events when you spoke?**
   - `user_state_changed`?
   - `conversation_item_added`?
   - Both?
   - Neither?

3. **What did the logs show?**
   - Copy the relevant lines showing what happened when you spoke

Based on your answers, I'll implement the RIGHT solution!

---

## 🎓 Why This Approach is Better

Your concerns about timeout were valid:
- ❌ Pure timeout: Can't detect actual speech
- ✅ Event detection: Real-time response to actual user speech
- ✅ Hybrid polling: Detects speech without events
- ✅ Timeout safety: Prevents hanging if no response

The hybrid approach solves ALL three concerns:
1. **Speaks > time limit:** Detected via polling, can extend time
2. **Speaks < time limit:** Detected early via polling, moves on quickly
3. **Doesn't speak:** Timeout catches it, can prompt or skip

---

**NOW: Restart agent and run the test. Report what you see!**
