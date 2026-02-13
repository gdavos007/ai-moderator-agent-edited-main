# Easy Multi-Participant Survey - Step-by-Step Guide

## ✅ **THE WORKING SOLUTION**

I've created `start_fresh_survey.py` which **AUTOMATICALLY**:
- ✅ Kills old agent processes (prevents dispatch conflicts!)
- ✅ Cleans up old rooms
- ✅ Generates clickable participant links

**No more manual cleanup needed!**

---

## 🚀 How to Use It (2 Simple Steps):

### **Step 1: Run the Script First**

```bash
python3 start_fresh_survey.py --participants 2
```

The script will:
1. Check for old agent processes
2. Kill them if found (and tell you to restart)
3. Clean up old rooms
4. Create fresh room and generate links

If it kills old agents, you'll see:
```
✅ Killed 1 agent process(es)
⏳ Please restart 'python3 agent.py dev' in Terminal 1
   Wait for 'registered worker' message, then run this script again.
```

---

### **Step 2: Start Agent (if prompted) and Re-run**

**Terminal 1** (if the script killed old agents):
```bash
python3 agent.py dev
```

Wait until you see:
```
INFO livekit.agents - registered worker {"id": "AW_xxxxx", ...}
```

**Terminal 2** (run the script again):
```bash
python3 start_fresh_survey.py --participants 2
```

You'll see:
1. **Cleanup phase**: Deletes old rooms
2. **Creation phase**: Creates fresh room
3. **Agent dispatch**: Sends agent to the room
4. **Participant links**: Clickable URLs for each participant

Example output:
```
🧹 CLEANING UP OLD ROOMS
✅ Cleaned up 2/2 room(s)

🆕 CREATING FRESH SURVEY ROOM
✅ Room created: survey-20251205-143200
✅ Agent dispatched: AD_Qiikxwjrhjq6

🔗 PARTICIPANT JOIN LINKS

👤 PARTICIPANT 1: Alice Johnson
📧 Send this link to Alice Johnson:
https://meet.livekit.io/custom?liveKitUrl=wss%3A%2F%2F...&token=eyJ...

👤 PARTICIPANT 2: Bob Smith
📧 Send this link to Bob Smith:
https://meet.livekit.io/custom?liveKitUrl=wss%3A%2F%2F...&token=eyJ...
```

---

### **Step 3: Verify Agent Joined**

In **Terminal 1**, you should see:
```
INFO livekit.agents - received job request {"job_id": "AJ_xxxxx", "room_name": "survey-20251205-143200", ...}
```

If you DON'T see this:
- Make sure only ONE `python3 agent.py dev` is running
- Run: `ps aux | grep "agent.py dev"` - should only show ONE process
- If multiple processes, kill all and restart Terminal 1

---

### **Step 4: Share Links with Participants**

Copy each participant's link and send it to them via:
- ✅ Email
- ✅ Slack/Teams
- ✅ Text message
- ✅ Shared document

**They just CLICK the link** - no copy-pasting tokens needed!

---

## 🔧 Troubleshooting

### Problem: Agent didn't join

**Symptom**: Terminal 1 doesn't show "received job request"

**Solution**: Just run the script again!
```bash
python3 start_fresh_survey.py --participants 2
```

The script will automatically:
1. Detect old agent processes
2. Kill them
3. Tell you to restart the agent
4. Clean everything up

**That's it!** No manual troubleshooting needed.

---

### Problem: Script hangs or no output

**Solution**: Press Ctrl+C and run again. The script will clean up old rooms automatically.

---

### Problem: Participants can't join

**Check**:
1. Are the links complete? They should start with `https://meet.livekit.io/custom?...`
2. Did participants click the ENTIRE link (not just part of it)?
3. Are the tokens still valid? (They expire after ~6 hours)

**Solution**: Run Terminal 2 again to generate fresh tokens.

---

## 📝 Different Number of Participants

For 3 participants:
```bash
python3 start_fresh_survey.py --participants 3
```

For 5 participants:
```bash
python3 start_fresh_survey.py --participants 5
```

Maximum: 8 participants

---

## 🎯 Quick Reference

| Terminal | Command | Purpose |
|----------|---------|---------|
| 1 | `python3 agent.py dev` | Run the AI moderator agent |
| 2 | `python3 start_fresh_survey.py --participants N` | Create room & generate links |

---

## ✅ Success Checklist

Before starting the survey, verify:

- [ ] Only ONE agent process is running (`ps aux | grep "agent.py dev"`)
- [ ] Terminal 1 shows "registered worker"
- [ ] Terminal 2 shows "Agent dispatched"
- [ ] Terminal 1 shows "received job request"
- [ ] You have the participant links ready to share
- [ ] Participants know to click the links (not copy-paste)

---

## 💡 Pro Tips

1. **Always use `start_fresh_survey.py`** - it cleans up automatically
2. **One agent at a time** - multiple agents cause issues
3. **Share whole links** - the URLs are long but complete
4. **Fresh tokens** - if unsure, just run Terminal 2 again

---

## 🆘 Still Having Issues?

Run this diagnostic:
```bash
echo "=== AGENT PROCESSES ==="
ps aux | grep "agent.py dev" | grep -v grep
echo ""
echo "=== SURVEY PROCESSES ==="
ps aux | grep "start_fresh_survey.py" | grep -v grep
```

There should be:
- Exactly 1 agent process
- 0 or 1 survey process (0 when not running, 1 when Terminal 2 is active)

If you see more, kill them all and start fresh.

---

**Bottom Line**: Use `start_fresh_survey.py` every time you want to start a new survey session. It handles all the cleanup automatically!
