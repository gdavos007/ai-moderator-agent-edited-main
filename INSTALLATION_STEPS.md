# Complete Installation Guide - Step by Step

Follow these steps in your **Terminal** to install everything needed for your AI Moderator Agent.

## 📍 Start Here

Open a new Terminal window and navigate to your project:

```bash
cd /Users/srinivasmagidewar/My_Docs/ai_projects/ai-moderator-agent
```

---

## Step 1: Install Homebrew (macOS Package Manager)

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

**What will happen:**
- You'll be prompted for your password
- Installation takes 5-10 minutes
- Follow the on-screen instructions

**After installation, add Homebrew to your PATH:**

```bash
# For Apple Silicon (M1/M2/M3 Mac)
echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> ~/.zprofile
eval "$(/opt/homebrew/bin/brew shellenv)"

# OR for Intel Mac
echo 'eval "$(/usr/local/bin/brew shellenv)"' >> ~/.zprofile
eval "$(/usr/local/bin/brew shellenv)"
```

**Verify:**
```bash
brew --version
# Should show: Homebrew 4.x.x
```

✅ **Checkpoint:** Homebrew installed

---

## Step 2: Install System Dependencies

```bash
# Install pkg-config and ffmpeg (required for audio/video processing)
brew install pkg-config ffmpeg

# Verify
pkg-config --version
ffmpeg -version
```

✅ **Checkpoint:** System dependencies installed

---

## Step 3: Install LiveKit CLI

```bash
brew install livekit-cli

# Verify
lk version
```

✅ **Checkpoint:** LiveKit CLI installed

---

## Step 4: Authenticate with LiveKit Cloud

```bash
lk cloud auth
```

**What will happen:**
- Opens a browser window
- You'll log into LiveKit Cloud
- CLI will be authenticated

✅ **Checkpoint:** LiveKit authenticated

---

## Step 5: Install Python Dependencies

```bash
# Activate your virtual environment
source ai_moderator_agent/bin/activate

# You should see (ai_moderator_agent) in your prompt

# Install all Python packages
pip install -r requirements.txt
```

**This installs:**
- LiveKit Agents framework
- OpenAI plugin
- Silero VAD
- Turn detector
- Noise cancellation
- And more...

**Installation takes:** 3-5 minutes

✅ **Checkpoint:** Python packages installed

---

## Step 6: Download Model Files

```bash
# Make sure environment is still activated
# You should see (ai_moderator_agent) in prompt

python agent.py download-files
```

**This downloads:**
- Silero VAD models (~10MB)
- Turn detection models (~20MB)
- Noise cancellation models (~5MB)

✅ **Checkpoint:** Model files downloaded

---

## Step 7: Verify Everything Works

```bash
# Test configuration
python verify_setup.py
```

**Expected output:**
```
✅ Found .env.local
✅ LiveKit WebSocket URL: wss://ai...
✅ LiveKit API Key: API...
✅ LiveKit API Secret: uHc...
✅ OpenAI API Key: sk-proj...
✅ Configuration looks good!
```

✅ **Checkpoint:** Configuration verified

---

## Step 8: Test Connectivity

```bash
python scripts/test_connection.py
```

**Expected output:**
```
✅ PASS - Environment Variables
✅ PASS - Configuration
✅ PASS - Module Imports
✅ PASS - LiveKit Connection
```

✅ **Checkpoint:** All systems ready

---

## 🎉 Step 9: Run Your Agent!

```bash
python agent.py dev
```

**Expected output:**
```
🚀 Starting AI Community Moderator Agent
Agent Name: CommunityModerator
LiveKit URL: wss://aiagentmoderator-bzu1wgs1.livekit.cloud
INFO - Worker starting...
INFO - Waiting for job requests...
```

**Keep this running** and proceed to testing!

---

## 🧪 Step 10: Test in Playground

1. Open browser: https://cloud.livekit.io
2. Navigate to: **Agents** → **Playground**
3. Click: **Start Session**
4. Your agent will join and greet you!
5. Try saying: "Hello!", "What's your role?", "Can you summarize?"

---

## 📋 Quick Reference

### Daily Usage

Every time you work on the agent:

```bash
# 1. Navigate to project
cd /Users/srinivasmagidewar/My_Docs/ai_projects/ai-moderator-agent

# 2. Activate environment
source ai_moderator_agent/bin/activate

# 3. Run agent
python agent.py dev

# 4. When done, deactivate
deactivate
```

### Helper Script

Or use the helper script:

```bash
source activate_env.sh
python agent.py dev
```

---

## 🆘 Troubleshooting

### "brew: command not found"
- Restart your terminal after installing Homebrew
- Or manually run: `eval "$(/opt/homebrew/bin/brew shellenv)"`

### "pkg-config: command not found"
- Make sure you ran: `brew install pkg-config ffmpeg`
- Verify: `which pkg-config`

### "No module named 'livekit'"
- Make sure environment is activated: `source ai_moderator_agent/bin/activate`
- Check prompt shows: `(ai_moderator_agent)`

### "Failed to connect to LiveKit"
- Run: `lk cloud auth`
- Verify `.env.local` has correct credentials

### "Insufficient credits" (OpenAI)
- Check billing: https://platform.openai.com/settings/organization/billing
- Add payment method or credits

---

## ✅ Installation Checklist

- [ ] Homebrew installed
- [ ] pkg-config installed
- [ ] ffmpeg installed
- [ ] LiveKit CLI installed
- [ ] LiveKit authenticated (`lk cloud auth`)
- [ ] Python environment activated
- [ ] Python packages installed
- [ ] Model files downloaded
- [ ] Configuration verified
- [ ] Connectivity tested
- [ ] Agent running successfully
- [ ] Tested in playground

---

## 🎯 Next Steps After Installation

1. **Customize the agent**: Edit `config/agent_config.py`
2. **Add custom tools**: Modify `src/moderator_agent.py`
3. **Build a frontend**: Create a web UI for your discussions
4. **Deploy to production**: Run `lk agent create`

---

## 💡 Tips

- Keep the agent running in one terminal tab
- Use another tab for development/testing
- Check logs in `logs/agent.log` for debugging
- Press `Ctrl+C` to stop the agent gracefully

---

**Need help?** Check:
- `README.md` - Full documentation
- `SETUP_COMPLETE.md` - Configuration details
- LiveKit Docs: https://docs.livekit.io
