#!/bin/bash
# Setup script for AI Moderator Agent

set -e

echo "🚀 Setting up AI Moderator Agent..."
echo ""

# Check if uv is installed
if ! command -v uv &> /dev/null; then
    echo "📦 Installing uv package manager..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.cargo/bin:$PATH"
fi

# Check if lk CLI is installed
if ! command -v lk &> /dev/null; then
    echo "📦 Installing LiveKit CLI..."
    if [[ "$OSTYPE" == "darwin"* ]]; then
        brew install livekit-cli
    elif [[ "$OSTYPE" == "linux-gnu"* ]]; then
        curl -sSL https://get.livekit.io/cli | bash
    else
        echo "⚠️  Please install LiveKit CLI manually from: https://github.com/livekit/livekit-cli"
    fi
fi

# Create logs directory if it doesn't exist
mkdir -p logs

# Install Python dependencies
echo ""
echo "📦 Installing Python dependencies..."
uv sync

# Check if .env.local exists
if [ ! -f .env.local ]; then
    echo ""
    echo "⚠️  .env.local not found. Creating from .env.example..."
    cp .env.example .env.local
    echo ""
    echo "📝 Please edit .env.local and add your API keys:"
    echo "   - LIVEKIT_API_KEY"
    echo "   - LIVEKIT_API_SECRET"
    echo "   - LIVEKIT_URL"
    echo "   - OPENAI_API_KEY"
    echo "   - ANTHROPIC_API_KEY"
    echo ""
fi

# Download model files
echo ""
echo "📥 Downloading model files..."
uv run agent.py download-files

echo ""
echo "✅ Setup complete!"
echo ""
echo "Next steps:"
echo "1. Edit .env.local with your API keys"
echo "2. Authenticate with LiveKit: lk cloud auth"
echo "3. Run the agent: uv run agent.py dev"
echo ""
