# Quick Start Guide

Get your AI Moderator Agent running in 5 minutes!

## Step 1: Update Your Environment Variables

Edit `.env.local` and replace the placeholder values with your actual API keys:

```bash
# LiveKit Configuration (from https://cloud.livekit.io)
LIVEKIT_API_KEY=APIsomething123
LIVEKIT_API_SECRET=secretkey123
LIVEKIT_URL=wss://your-project.livekit.cloud

# OpenAI Configuration (from https://platform.openai.com/api-keys)
OPENAI_API_KEY=sk-proj-something123

# Anthropic Configuration (from https://console.anthropic.com/account/keys)
ANTHROPIC_API_KEY=sk-ant-something123
```

## Step 2: Install Dependencies

```bash
# If you haven't installed uv yet
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install project dependencies
uv sync
```

## Step 3: Install LiveKit CLI and Authenticate

```bash
# macOS
brew install livekit-cli

# Linux
curl -sSL https://get.livekit.io/cli | bash

# Authenticate with your LiveKit Cloud account
lk cloud auth
```

## Step 4: Download Model Files

```bash
uv run agent.py download-files
```

## Step 5: Test Your Configuration

```bash
uv run python scripts/test_connection.py
```

You should see all tests passing ✅

## Step 6: Run the Agent

```bash
uv run agent.py dev
```

You should see output like:
```
Starting AI Community Moderator Agent
Agent Name: CommunityModerator
Agent Identity: moderator-bot
LiveKit URL: wss://your-project.livekit.cloud
INFO - Worker starting...
INFO - Waiting for job requests...
```

## Step 7: Test in the Playground

1. Open your browser to: https://cloud.livekit.io
2. Navigate to **Agents** → **Playground**
3. Click **Start Session**
4. Your moderator agent will join the room
5. Try speaking to it!

## What to Say to Test

Try these phrases to test different features:

- "Hello!" - The agent will greet you
- "Can you summarize our discussion?" - Tests summary feature
- Start talking about random topics - Tests topic redirection
- Say inappropriate content (carefully!) - Tests content flagging

## Next Steps

### Customize the Agent

Edit `config/agent_config.py` to change:
- Agent personality and instructions
- Model selections
- Behavior settings

### Add Custom Tools

Edit `src/moderator_agent.py` to add new moderation tools

### Build a Frontend

Create a web interface for your community discussions. Examples:
- Next.js: https://docs.livekit.io/home/quickstarts/nextjs
- React: https://docs.livekit.io/home/quickstarts/react
- Any framework with LiveKit Web SDK

### Deploy to Production

```bash
lk agent create
```

This will:
1. Create a Dockerfile
2. Build your agent
3. Deploy to LiveKit Cloud
4. Make it available globally

## Troubleshooting

### "Missing required environment variables"
- Double-check `.env.local` has all required keys
- Make sure there are no typos
- Ensure the file is in the project root

### "Failed to connect to LiveKit"
- Run `lk cloud auth` to authenticate
- Verify your LiveKit URL is correct
- Check your API key and secret

### Model download issues
- Run `uv run agent.py download-files` again
- Check your internet connection

### Agent not responding
- Check the logs in `logs/agent.log`
- Verify all API keys are valid
- Make sure you have credits on OpenAI/Anthropic

## Getting Help

- LiveKit Docs: https://docs.livekit.io
- LiveKit Community: https://livekit.io/community
- Project README: See README.md for full documentation

## Common Commands

```bash
# Test configuration
uv run python scripts/test_connection.py

# Run in development mode
uv run agent.py dev

# Download model files
uv run agent.py download-files

# Deploy to production
lk agent create

# View logs
tail -f logs/agent.log
```

Happy moderating! 🎉
