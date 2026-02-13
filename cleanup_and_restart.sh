#!/bin/bash
# Complete cleanup script for AI Survey Moderator
# Run this between survey sessions to ensure clean state

echo "================================================================================"
echo "🧹 AI SURVEY MODERATOR - COMPLETE CLEANUP"
echo "================================================================================"
echo ""

# Step 1: Kill all agent processes
echo "Step 1: Killing all agent processes..."
pkill -9 -f "agent.py"
sleep 1

# Step 2: Kill ALL multiprocessing child processes (forkserver, spawn, resource_tracker)
echo "Step 2: Killing all multiprocessing child processes..."
pkill -9 -f "multiprocessing.forkserver"
pkill -9 -f "multiprocessing.spawn"
pkill -9 -f "multiprocessing.resource_tracker"
sleep 2

# Step 3: Verify no processes remain
echo ""
echo "Step 3: Verifying all processes are killed..."
AGENT_COUNT=$(ps aux | grep -E "agent.py|multiprocessing" | grep -v grep | grep -v cleanup | wc -l | tr -d ' ')

if [ "$AGENT_COUNT" -eq 0 ]; then
    echo "✅ All agent processes killed successfully"
else
    echo "⚠️  WARNING: $AGENT_COUNT process(es) still running:"
    ps aux | grep -E "agent.py|multiprocessing" | grep -v grep | grep -v cleanup
    echo ""
    echo "Attempting force kill of remaining processes..."
    ps aux | grep -E "agent.py|multiprocessing" | grep -v grep | grep -v cleanup | awk '{print $2}' | xargs kill -9 2>/dev/null
    sleep 1
fi

# Step 4: Clean up all LiveKit rooms
echo ""
echo "Step 4: Cleaning up all LiveKit rooms..."
python3 cleanup_all_rooms.py

# Step 5: Final verification
echo ""
echo "Step 5: Final verification..."
echo "───────────────────────────────────────────────────────────────────────────────"

# Check processes
FINAL_COUNT=$(ps aux | grep -E "agent.py|multiprocessing" | grep -v grep | grep -v cleanup | wc -l | tr -d ' ')
if [ "$FINAL_COUNT" -eq 0 ]; then
    echo "✅ Process check: Clean (0 processes)"
else
    echo "❌ Process check: $FINAL_COUNT process(es) still running!"
    ps aux | grep -E "agent.py|multiprocessing" | grep -v grep | grep -v cleanup
fi

echo ""
echo "================================================================================"
echo "✅ CLEANUP COMPLETE - Ready for fresh start!"
echo "================================================================================"
echo ""
echo "Next steps:"
echo "  Terminal 1: python3 agent.py dev"
echo "  Terminal 2: python3 start_fresh_survey.py --participants <N>"
echo ""
