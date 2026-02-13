# Survey Moderator Agent - Changes Documentation

## Overview

The AI Moderator Agent has been updated to conduct structured surveys with the following key features:

1. **Question-Based Moderation**: Agent only asks questions from a file, does not engage in discussion
2. **Random Participant Selection**: Participants are selected in random order for each question
3. **Improved Interruption Logic**: More aggressive time limit enforcement
4. **Neutral Stance**: Agent politely declines to discuss or provide opinions

---

## What Changed

### 1. New Components

#### `src/question_loader.py`
- Loads survey questions from Word documents (.docx files)
- Supports random or sequential question selection
- Tracks which questions have been asked
- Located in `ai_moderator_agent/topic_questions/` directory

#### `src/participant_manager.py`
- Manages participant selection and tracking
- Ensures random order for each question
- Tracks speaking time and questions answered per participant
- Prevents same order repetition across questions

#### `src/moderator_agent_v2.py`
- New survey moderator agent implementation
- Integrates question loader and participant manager
- Simplified interruption logic (removed topic enforcement)
- Focused on time management only

### 2. Updated Components

#### `config/agent_config.py`
- **New Instructions**: Agent is now explicitly instructed to:
  - ONLY ask questions (not discuss)
  - Politely decline if participants ask for opinions
  - Stay neutral and not comment on responses
  - Be firm and direct with time limits
  - Use brief acknowledgments only

#### `agent.py`
- Switched from `create_moderator_session()` to `create_survey_moderator_session()`
- Added initial greeting that explains survey format
- Automatically starts asking questions after 5-second wait
- Simplified configuration (removed topic enforcement parameters)

---

## Key Features

### Random Participant Selection

**How it works:**
- Question 1: Might ask Participant A, then D, then B, then C
- Question 2: Might ask Participant C, then A, then D, then B
- Each question gets a different random order
- All participants must answer before moving to next question

**Example Flow:**
```
Q1: "Do you think Orange County is on the right track?"
  → Participant_3 answers
  → Participant_1 answers
  → Participant_2 answers

Q2: "What are the top priorities for Orange County?"
  → Participant_2 answers (different order!)
  → Participant_3 answers
  → Participant_1 answers
```

### Improved Time Limit Enforcement

**Old Behavior:**
- 3-stage interruption (warning, first interrupt, force end)
- Multiple grace periods
- Often failed to actually interrupt

**New Behavior:**
- Single-stage: Interrupt immediately at max_turn_duration (20 seconds default)
- Firm, direct language: "Thank you [name], we need to move on"
- If participant continues after 3 seconds, automatically move to next participant
- No apologies or excessive politeness

**Configuration:**
- `max_turn_duration`: 20 seconds (for testing)
- Change to 30-60 seconds for production

### No-Discussion Policy

**If participant asks agent to discuss:**
- Agent response: "I'm here to collect your valuable feedback, not to share my own views. What are YOUR thoughts on this?"

**If participant asks for elaboration:**
- Agent response: "I appreciate the question, but my role is to facilitate this survey, not participate in it."

**Communication style:**
- Brief acknowledgments only: "Thank you", "Noted", "I see"
- NO evaluative comments: "That's interesting", "Great point"
- NO elaboration on questions
- Move quickly between participants

---

## File Structure

```
ai-moderator-agent/
├── agent.py                          # Main entry point (UPDATED)
├── config/
│   └── agent_config.py               # Configuration (UPDATED)
├── src/
│   ├── moderator_agent.py            # Old version (kept for reference)
│   ├── moderator_agent_v2.py         # NEW: Survey moderator
│   ├── question_loader.py            # NEW: Question management
│   ├── participant_manager.py        # NEW: Participant selection
│   └── utils.py
└── ai_moderator_agent/
    └── topic_questions/               # Question files directory
        └── Resident and Employee Survey.docx
```

---

## Usage

### 1. Add Questions

Place your survey questions in a Word document (.docx) in the `ai_moderator_agent/topic_questions/` directory.

**Question Format:**
- Questions are automatically detected (lines containing '?')
- Must be longer than 20 characters
- Example:
  ```
  Do you think that Orange County is on the right track or on the wrong track?

  What should be the top priorities of Orange County's elected leaders?
  ```

### 2. Deploy Agent

```bash
# Deploy to LiveKit Cloud
lk agent deploy --secrets-file .env.local

# Or run locally for testing
source ai_moderator_agent/bin/activate
python agent.py dev
```

### 3. Test in Playground

1. Go to https://agents-playground.livekit.io
2. Select your project
3. Click "Connect"
4. Wait for agent to greet you
5. Agent will start asking questions in random participant order

---

## Configuration

### Environment Variables (.env.local)

```bash
# Required
LIVEKIT_URL=wss://your-project.livekit.cloud
LIVEKIT_API_KEY=your_api_key
LIVEKIT_API_SECRET=your_api_secret
OPENAI_API_KEY=your_openai_key

# Time limits
MAX_TURN_DURATION=20           # Seconds per turn (20 for testing, 30-60 for prod)
ENABLE_TURN_LIMITS=true        # Enable/disable time limits

# Agent identity
AGENT_NAME=SurveyModerator
AGENT_IDENTITY=survey-bot
```

### Adjusting Time Limits

For testing (quick responses):
```bash
MAX_TURN_DURATION=20
```

For production (detailed responses):
```bash
MAX_TURN_DURATION=60
```

---

## Troubleshooting

### Agent Not Interrupting

**Symptoms:**
- Participants speak beyond time limit
- Agent doesn't interrupt

**Solutions:**
1. Check `ENABLE_TURN_LIMITS=true` in `.env.local`
2. Lower `MAX_TURN_DURATION` for testing (try 10-15 seconds)
3. Check logs: `lk agent logs --log-type deploy | grep INTERRUPT`
4. Note: Agent can only speak over participant, cannot actually mute their mic

### Questions Not Loading

**Symptoms:**
- Agent fails to start
- Error: "Failed to load questions"

**Solutions:**
1. Verify Word file exists: `ai_moderator_agent/topic_questions/*.docx`
2. Check file permissions
3. Ensure questions contain `?` character
4. Check logs: `lk agent logs --log-type deploy | grep question`

### Agent Discusses Topics Instead of Asking Questions

**Symptoms:**
- Agent provides opinions
- Agent elaborates on responses

**Solutions:**
1. Redeploy agent: `lk agent deploy --secrets-file .env.local`
2. Verify using updated `agent.py` (should import `moderator_agent_v2`)
3. Check logs for correct initialization

---

## Testing Checklist

- [ ] Agent loads questions successfully
- [ ] Agent greets participants briefly
- [ ] Agent asks first question to random participant
- [ ] Agent interrupts at time limit (test with long response)
- [ ] Agent moves to next participant in random order
- [ ] Agent asks next question after all participants answer
- [ ] Agent declines to discuss when asked
- [ ] Agent provides only brief acknowledgments
- [ ] All participants get turns in random orders

---

## Future Enhancements

Potential improvements:
1. **Question Branching**: Skip questions based on previous answers
2. **Dynamic Time Limits**: Adjust based on question complexity
3. **Participant Metrics**: Export speaking time and participation stats
4. **Multi-Language**: Support questions in multiple languages
5. **Visual Cues**: Integration with video for hand-raise detection

---

## Migration from Old Version

If you want to revert to the old discussion-based moderator:

1. Edit `agent.py`:
   ```python
   # Change this line:
   from src.moderator_agent_v2 import create_survey_moderator_session

   # Back to:
   from src.moderator_agent import create_moderator_session
   ```

2. Update the session creation call to use old parameters

3. Redeploy: `lk agent deploy --secrets-file .env.local`

---

## Questions?

- LiveKit Agents Docs: https://docs.livekit.io/agents
- Project GitHub: (your repo here)

**Last Updated**: November 2025
