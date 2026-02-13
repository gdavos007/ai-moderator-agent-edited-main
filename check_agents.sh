#!/bin/bash
# Check how many agent processes are running

echo "Checking for agent.py processes..."
echo ""

# Count agent processes
count=$(ps aux | grep "python.*agent.py dev" | grep -v grep | wc -l | tr -d ' ')

if [ "$count" -eq 0 ]; then
    echo "❌ NO agent processes running"
    echo ""
    echo "Start the agent with: python3 agent.py dev"
elif [ "$count" -eq 1 ]; then
    echo "✅ GOOD: Exactly 1 agent process running"
    echo ""
    ps aux | grep "python.*agent.py dev" | grep -v grep
else
    echo "⚠️  WARNING: $count agent processes found!"
    echo ""
    echo "Multiple agents will cause conflicts. Here they are:"
    echo ""
    ps aux | grep "python.*agent.py dev" | grep -v grep
    echo ""
    echo "Kill all agents with: pkill -f 'agent.py dev'"
fi

echo ""
