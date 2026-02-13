#!/bin/bash
# Quick activation script for the AI Moderator Agent environment

echo "🔧 Activating ai_moderator_agent environment..."
source ai_moderator_agent/bin/activate

echo "✅ Environment activated!"
echo ""
echo "Python: $(which python)"
echo "Version: $(python --version)"
echo ""
echo "Available commands:"
echo "  python agent.py dev          - Run agent in development mode"
echo "  python agent.py download-files - Download model files"
echo "  python verify_setup.py       - Verify configuration"
echo "  deactivate                   - Exit this environment"
echo ""
