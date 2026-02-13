#!/bin/bash
#
# Start Agent Script - Ensures multiprocessing uses 'fork' method
# This solves the BrokenPipeError on macOS
#

cd /Users/srinivasmagidewar/My_Docs/ai_projects/ai-moderator-agent

# Activate virtual environment
source ai_moderator_agent/bin/activate

# Add current directory to PYTHONPATH so sitecustomize.py is loaded
export PYTHONPATH=/Users/srinivasmagidewar/My_Docs/ai_projects/ai-moderator-agent:$PYTHONPATH

echo "=========================================="
echo "Starting AI Survey Moderator Agent"
echo "=========================================="
echo "✅ Using 'fork' multiprocessing method (sitecustomize.py)"
echo ""

# Start the agent
python agent.py dev
