# Easy Multi-Participant Join - Complete Guide

After extensive testing, here's what we learned:

## ❌ What DOESN'T Work:
1. **meet.livekit.io URLs** - URL encoding breaks tokens
2. **meet.livekit.io/custom POST** - Returns 400 Bad Request
3. **Copying URLs into address bar** - Browsers decode the token

## ✅ What WORKS:

### Option 1: LiveKit Agents Playground (Current Working Solution)
**Best for: Testing with 2-3 participants**

Participants use: https://agents-playground.livekit.io
- Click "Manual" tab
- Paste LiveKit URL and Token
- Click Connect

**Pros:** Works reliably, shows agent clearly
**Cons:** Requires copy-pasting tokens (not user-friendly for many participants)

### Option 2: Build Your Own Web App (Recommended for Real Surveys)
**Best for: Production use with many participants**

You need to create a simple web app using LiveKit's JavaScript SDK that:
1. Takes the token as a URL parameter
2. Connects to LiveKit directly
3. Provides a simple UI

**Steps:**
```bash
# 1. Create a simple Express server
npm init -y
npm install express @livekit/components-react livekit-client

# 2. Create HTML page that uses LiveKit Web SDK
# 3. Host on Vercel, Netlify, or your own server
# 4. Generate URLs like: https://your-app.com/join?token=...
```

**Example repository:** https://github.com/livekit-examples/meet

### Option 3: Use Zoom Integration (Your zoom_survey.py)
**Best for: Participants already on Zoom**

Your existing `zoom_survey.py` script allows participants to:
1. Join a regular Zoom meeting
2. The Recall.ai bot bridges them to LiveKit
3. Your AI agent moderates the survey

**This is the easiest for participants!** They just join a normal Zoom meeting.

## 🎯 RECOMMENDED APPROACH FOR YOUR USE CASE:

Given that you want **easy multi-participant joining**, I recommend:

### SHORT TERM (Testing):
Use the **Agents Playground** method we just tested successfully:
- Run: `python3 join_survey_playground.py`
- Share tokens via email/Slack
- Participants paste into playground

### LONG TERM (Production):
Implement the **Zoom bridge** approach:
1. Use your existing `zoom_survey.py`
2. Participants join via normal Zoom link
3. Recall.ai bot bridges to LiveKit
4. AI agent conducts survey

**Why Zoom?**
- ✅ Everyone knows how to use Zoom
- ✅ No special setup needed
- ✅ Works on all devices
- ✅ Participants just click a Zoom link
- ✅ You already have the code!

## Quick Start Commands:

### For Testing (2-3 people):
```bash
# Terminal 1
python3 agent.py dev

# Terminal 2
python3 join_survey_playground.py

# Share tokens with participants
```

### For Real Surveys (10+ people):
```bash
# Terminal 1
python3 agent.py dev

# Terminal 2
python3 zoom_survey.py --zoom-url "https://zoom.us/j/YOUR_MEETING_ID"

# Share Zoom link with participants
```

## Need Help?

1. **For Zoom setup:** See ZOOM_SETUP.md
2. **For web app:** LiveKit provides example apps at https://github.com/livekit-examples
3. **For custom solution:** Contact me for implementation help

---

**Bottom line:** For easy participant joining, use **Zoom** or build a **custom web app**. The playground is great for testing but not scalable for many participants.
