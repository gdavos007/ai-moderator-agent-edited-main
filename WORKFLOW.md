# AI Survey Moderator - Recommended Workflow

## Between Each Survey Session

**ALWAYS run the cleanup script between sessions:**

```bash
./cleanup_and_restart.sh
```

This ensures:
- ✅ All agent processes are killed
- ✅ All forkserver child processes are killed
- ✅ All LiveKit rooms are cleaned up
- ✅ System is ready for a fresh start

## Starting a New Survey Session

### Terminal 1 - Start Agent Worker
```bash
python3 agent.py dev
```

**Wait for:** `Worker registered, waiting for jobs...`

### Terminal 2 - Start Survey
```bash
python3 start_fresh_survey.py --participants 2
```

**You should see:**
- ✅ Only 1 agent process found
- ✅ Agent dispatched successfully
- ✅ Agent count verification shows 0 or 1 agent (not 2!)

### Terminal 1 - Verify Single Agent
Look for **ONE** Job ID like:
```
Job ID: AJ_xxxxxxxxxxxxx
Process PID: 12345
```

**If you see TWO Job IDs = PROBLEM!** Stop and run cleanup again.

## Troubleshooting

### Problem: Two Agents Joining

**Cause:** Stale forkserver processes from previous session

**Solution:**
1. Stop both terminals (Ctrl+C)
2. Run `./cleanup_and_restart.sh`
3. Verify output shows `✅ Process check: Clean (0 processes)`
4. Start fresh with Terminal 1, then Terminal 2

### Problem: Agent Not Joining

**Cause:** Agent worker not running or stuck

**Solution:**
1. Check Terminal 1 shows "Worker registered, waiting for jobs..."
2. If not, run cleanup and restart Terminal 1
3. If yes, check Terminal 2 dispatch logs

### Problem: Processes Won't Die

**Manual force kill:**
```bash
ps aux | grep -i "agent.py\|forkserver" | grep -v grep | awk '{print $2}' | xargs kill -9
```

## Quick Reference

| Command | Purpose |
|---------|---------|
| `./cleanup_and_restart.sh` | Clean everything between sessions |
| `./check_agents.sh` | Quick check if agent is running |
| `./debug_agents.sh` | Detailed process tree view |
| `python3 cleanup_all_rooms.py` | Delete all LiveKit rooms |

## Best Practices

1. ✅ **Always cleanup between sessions** - Don't skip this!
2. ✅ **Wait for "Worker registered"** before starting Terminal 2
3. ✅ **Check Terminal 1 logs** to verify only ONE Job ID
4. ✅ **Keep Terminal 1 running** throughout the survey
5. ❌ **Don't restart Terminal 1** mid-survey unless necessary

## Why Cleanup Is Critical

The LiveKit Agents framework uses multiprocessing with forkserver. When you stop a survey:
- The main `agent.py` process dies
- But forkserver **child processes** can survive
- These zombie processes will accept new jobs
- This causes duplicate agents in the next session

**The cleanup script ensures ALL processes (parent + children) are killed.**
