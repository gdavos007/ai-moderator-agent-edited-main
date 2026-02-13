# Quick Start: Survey Moderator Agent

## What's New?

Your moderator agent is now a **Survey Moderator** that:

✅ **Asks questions from Word document** (already loaded: "Resident and Employee Survey.docx")
✅ **Selects participants in random order** for each question
✅ **Interrupts firmly at 20-second time limit**
✅ **Does NOT discuss topics** - only collects responses
✅ **Politely declines** if asked to provide opinions

---

## Deploy Now

```bash
cd /Users/srinivasmagidewar/My_Docs/ai_projects/ai-moderator-agent

# Activate environment
source ai_moderator_agent/bin/activate

# Deploy to cloud
lk agent deploy --secrets-file .env.local
```

---

## Test It

1. Go to: https://agents-playground.livekit.io
2. Select project: "AI_Agent_Moderator"
3. Click "Connect"
4. Agent will:
   - Greet you
   - Wait 5 seconds
   - Ask first question to a random participant
   - Interrupt at 20 seconds if response is too long
   - Move to next participant in random order

---

## Key Behavior Changes

### OLD Behavior:
- Agent engaged in discussions
- Commented on responses ("That's interesting!")
- Multiple interruption attempts (often failed)
- Fixed participant order

### NEW Behavior:
- Agent ONLY asks questions
- Brief acknowledgments only ("Thank you", "Noted")
- Single firm interruption at 20 seconds
- Random participant order for each question

---

## Files Changed

```
✅ agent.py                       - Now uses survey moderator
✅ config/agent_config.py         - Updated instructions (no discussion)
✅ src/moderator_agent_v2.py      - NEW survey moderator implementation
✅ src/question_loader.py         - NEW question management
✅ src/participant_manager.py     - NEW random participant selection
```

---

## Your Questions Are Already Loaded

Questions are loaded from:
```
ai_moderator_agent/topic_questions/Resident and Employee Survey.docx
```

The agent automatically:
- Reads all questions from the file
- Asks them in order
- Selects participants randomly for each question

---

## Adjust Time Limits

Current: **20 seconds** (good for testing)

To change:
```bash
# Edit .env.local
MAX_TURN_DURATION=30   # Change to 30, 45, or 60 seconds
```

Then redeploy:
```bash
lk agent deploy --secrets-file .env.local
```

---

## Example Session

```
Agent: "Hello! I'm the AI survey moderator. I'll be asking survey
       questions. Each person has about 20 seconds to respond.
       Let's begin!"

Agent: [waits 5 seconds]

Agent: "John, do you think that Orange County is on the right track
       or on the wrong track?"

John: [Speaks for 18 seconds]

Agent: "Thank you, noted. Sarah, same question: Do you think that
       Orange County is on the right track or on the wrong track?"

Sarah: [Speaks for 25 seconds]

Agent: [At 20 seconds] "Thank you Sarah, we need to move on.
       Please wrap up in one sentence."

[After all participants answer Question 1]

Agent: "Mike, what should be the top priorities of Orange County's
       elected leaders? Choose THREE."

[Process continues with random participant order]
```

---

## If Participant Asks Agent to Discuss

**Participant**: "What do YOU think about this issue?"

**Agent**: "I'm here to collect your valuable feedback, not to share
my own views. What are YOUR thoughts on this?"

---

## Troubleshooting

### Agent Not Interrupting?
```bash
# Lower the time limit for testing
echo "MAX_TURN_DURATION=15" >> .env.local
lk agent deploy --secrets-file .env.local
```

### Want to see what's happening?
```bash
# View live logs
lk agent logs --log-type deploy

# Look for these messages:
# - "Loaded X questions"
# - "Selected participant: X for question Y"
# - "TIME LIMIT EXCEEDED: ... INTERRUPTING"
```

### Questions not loading?
```bash
# Verify file exists
ls -la ai_moderator_agent/topic_questions/

# Should see: "Resident and Employee Survey.docx"
```

---

## Full Documentation

See `SURVEY_MODE_CHANGES.md` for complete details on:
- All changes made
- Configuration options
- Testing checklist
- How to revert to old version

---

**Ready to test? Deploy and try it now!**

```bash
lk agent deploy --secrets-file .env.local
```

Then visit: https://agents-playground.livekit.io
