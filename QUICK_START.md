# Quick Start - Multi-Participant Survey

## 🎯 Simplest Way to Run a Survey

### Option 1: First Time (No Agent Running)

**Terminal 1:**
```bash
python3 agent.py dev
```

**Terminal 2** (wait for "registered worker"):
```bash
python3 start_fresh_survey.py --participants 2
```

---

### Option 2: Just Run the Script (It Handles Everything!)

```bash
python3 start_fresh_survey.py --participants 2
```

The script will:
- ✅ Kill old agents if needed (and tell you to restart)
- ✅ Clean up old rooms
- ✅ Create fresh room
- ✅ Generate participant links

If it killed old agents, just:
1. Start agent: `python3 agent.py dev`
2. Run script again: `python3 start_fresh_survey.py --participants 2`

---

## 📧 Share the Links

Copy each link and send to participants:
```
👤 PARTICIPANT 1: Alice Johnson
https://meet.livekit.io/custom?liveKitUrl=...&token=...

👤 PARTICIPANT 2: Bob Smith
https://meet.livekit.io/custom?liveKitUrl=...&token=...
```

They just **click and join** - that's it!

---

## ✅ Success = See This in Terminal 1

```
INFO livekit.agents - received job request {"room_name": "survey-20251205-143200", ...}
```

---

## 📖 More Details

See **EASY_MULTI_PARTICIPANT_GUIDE.md** for:
- Detailed troubleshooting
- How it works
- Advanced options

---

**Bottom Line**: Run `python3 start_fresh_survey.py --participants N` and follow the prompts!
