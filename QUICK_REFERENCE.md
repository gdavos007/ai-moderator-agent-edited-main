# AI Community Moderator - Quick Reference

## Common Commands

### Deployment
```bash
# Deploy code changes
lk agent deploy --secrets-file .env.local

# Check status
lk agent status

# View logs
lk agent logs --log-type deploy

# Restart agent
lk agent restart

# Rollback to previous version
lk agent rollback
```

### Local Testing
```bash
# Activate environment
source ai_moderator_agent/bin/activate

# Run locally
python agent.py dev

# Download model files
python agent.py download-files
```

### Monitoring
```bash
# List all agents
lk agent list

# Check agent versions
lk agent versions

# View secrets (keys only)
lk agent secrets
```

---

## How Users Join Discussions

### Option 1: Agents Playground (Testing)
1. Visit: https://agents-playground.livekit.io
2. Select project: "AI_Agent_Moderator"
3. Click "Connect"
4. Wait 10-20 seconds for agent to join
5. Start speaking!

### Option 2: Custom Web App
```javascript
import { Room } from 'livekit-client';

const room = new Room();
await room.connect(livekitURL, token);
// Agent joins automatically
```

### Option 3: Programmatic
```python
from livekit import api

api.room.create_room(api.CreateRoomRequest(name="my-room"))
# Agent joins automatically
```

---

## Agent Behavior

**On Room Join**:
1. Agent automatically joins (10-20 sec cold start)
2. Greets participants
3. Introduces itself as moderator
4. Begins listening

**During Discussion**:
- Listens to all participants
- Responds when spoken to
- Helps facilitate conversation
- Maintains professional tone

---

## Current Configuration

**Deployed Agent**:
- ID: `CA_KJkgkznW5PD8`
- Project: aiagentmoderator
- Region: US East B
- Status: ✅ Running

**AI Models**:
- STT: gpt-4o-transcribe
- LLM: gpt-4o-mini
- TTS: gpt-4o-mini-tts (ash voice)

**Features**:
- ✅ Automatic room dispatch
- ✅ Voice activity detection
- ✅ Noise cancellation
- ✅ Turn detection

---

## File Locations

```
📁 Project Root: /Users/srinivasmagidewar/My_Docs/ai_projects/ai-moderator-agent/

📄 Key Files:
├── agent.py                  # Main entry point
├── src/moderator_agent.py    # Agent implementation
├── config/agent_config.py    # Configuration
├── .env.local               # API keys (DO NOT COMMIT)
├── requirements.txt         # Dependencies
├── Dockerfile              # Docker config
└── livekit.toml           # Agent ID & project

📁 Virtual Environment:
└── ai_moderator_agent/     # Python packages
```

---

## URLs & Links

- **Playground**: https://agents-playground.livekit.io
- **LiveKit Cloud**: https://cloud.livekit.io
- **Your Project**: wss://aiagentmoderator-bzu1wgs1.livekit.cloud
- **LiveKit Docs**: https://docs.livekit.io/agents
- **OpenAI Platform**: https://platform.openai.com

---

## Troubleshooting Quick Fixes

**Agent not joining?**
```bash
lk agent status  # Check if Running
lk agent logs --log-type deploy | grep ERROR
```

**Need to update secrets?**
```bash
lk agent update --secrets-file .env.local
```

**Code changes not applying?**
```bash
lk agent deploy --secrets-file .env.local
```

**SSL errors (macOS)?**
```bash
/Applications/Python\ 3.12/Install\ Certificates.command
```

---

## Cost Estimates (Approximate)

**Per Conversation Hour**:
- STT: ~$0.01-0.05 (gpt-4o-transcribe)
- LLM: ~$0.01-0.10 (gpt-4o-mini)
- TTS: ~$0.10-0.50 (gpt-4o-mini-tts)
- **Total**: ~$0.12-0.65/hour per active conversation

**LiveKit Cloud**:
- Free tier: 10,000 minutes/month
- Build plan: $29/month + usage

---

## Emergency Commands

**Stop agent immediately**:
```bash
lk agent delete --id CA_KJkgkznW5PD8
```

**Redeploy from scratch**:
```bash
rm livekit.toml
lk agent create --secrets-file .env.local
```

---

*Last Updated: October 28, 2025*
