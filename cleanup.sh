#!/bin/bash
#
# Cleanup Script for AI Moderator Agent
# Kills running processes and clears Python cache
#

echo "=========================================="
echo "AI Moderator Agent - Cleanup Script"
echo "=========================================="
echo ""

# Step 1: Kill running agent processes
echo "🔍 Checking for running agent processes..."
AGENT_PIDS=$(ps aux | grep "agent.py" | grep -v grep | awk '{print $2}')

if [ -z "$AGENT_PIDS" ]; then
    echo "   ✅ No running agent processes found"
else
    echo "   🛑 Killing agent processes: $AGENT_PIDS"
    pkill -f "agent.py"
    sleep 2
    echo "   ✅ Agent processes killed"
fi

# Step 2: Clear Python cache
echo ""
echo "🧹 Clearing Python cache files..."

# Remove __pycache__ directories (excluding virtualenv)
CACHE_DIRS=$(find . -type d -name "__pycache__" -not -path "./ai_moderator_agent/*" 2>/dev/null | wc -l)
if [ "$CACHE_DIRS" -gt 0 ]; then
    find . -type d -name "__pycache__" -not -path "./ai_moderator_agent/*" -exec rm -rf {} + 2>/dev/null
    echo "   ✅ Removed $CACHE_DIRS __pycache__ directories"
else
    echo "   ✅ No cache directories to remove"
fi

# Remove .pyc files (excluding virtualenv)
PYC_FILES=$(find . -name "*.pyc" -not -path "./ai_moderator_agent/*" 2>/dev/null | wc -l)
if [ "$PYC_FILES" -gt 0 ]; then
    find . -name "*.pyc" -not -path "./ai_moderator_agent/*" -delete 2>/dev/null
    echo "   ✅ Removed $PYC_FILES .pyc files"
else
    echo "   ✅ No .pyc files to remove"
fi

# Step 3: Summary
echo ""
echo "=========================================="
echo "✅ Cleanup Complete!"
echo "=========================================="
echo ""
echo "To start fresh:"
echo "  Terminal 1: python agent.py dev"
echo "  Terminal 2: python join_survey.py"
echo ""
