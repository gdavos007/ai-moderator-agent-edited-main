#!/bin/bash
# Debug script to see all agent-related processes

echo "==================================================================="
echo "DEBUGGING AGENT PROCESSES"
echo "==================================================================="
echo ""

echo "1. All Python processes containing 'agent':"
echo "-------------------------------------------------------------------"
ps aux | grep -i "python.*agent" | grep -v grep | grep -v debug_agents
echo ""

echo "2. All processes from agent.py:"
echo "-------------------------------------------------------------------"
ps aux | grep "agent.py" | grep -v grep
echo ""

echo "3. LiveKit-related processes:"
echo "-------------------------------------------------------------------"
ps aux | grep -i "livekit" | grep -v grep
echo ""

echo "4. Count of agent.py processes:"
echo "-------------------------------------------------------------------"
count=$(ps aux | grep "agent.py dev" | grep -v grep | wc -l | tr -d ' ')
echo "Found: $count process(es)"
echo ""

if [ "$count" -gt 1 ]; then
    echo "⚠️  WARNING: Multiple agent.py processes detected!"
    echo "This will cause duplicate agents in the room."
    echo ""
    echo "PIDs:"
    ps aux | grep "agent.py dev" | grep -v grep | awk '{print $2}'
fi

echo "==================================================================="
