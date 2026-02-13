#!/bin/bash
# Run the agent in development mode

echo "🚀 Starting AI Moderator Agent in development mode..."
echo ""

# Check if .env.local exists
if [ ! -f .env.local ]; then
    echo "❌ .env.local not found!"
    echo "Please copy .env.example to .env.local and configure your API keys."
    exit 1
fi

# Create logs directory if it doesn't exist
mkdir -p logs

# Run the agent
uv run agent.py dev
