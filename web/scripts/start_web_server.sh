#!/bin/bash
# ============================================================
# Start Survey Web Server
# ============================================================
#
# This script starts the web server for the Zoom-like join experience.
#
# Usage:
#   ./start_web_server.sh              # Start on default port 8080
#   ./start_web_server.sh 3000         # Start on custom port
#
# For external access, use ngrok in another terminal:
#   ngrok http 8080
#
# ============================================================

set -e

# Get script directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
WEB_DIR="$( cd "$SCRIPT_DIR/.." && pwd )"
PROJECT_DIR="$( cd "$WEB_DIR/.." && pwd )"

# Default port
PORT="${1:-8080}"

echo ""
echo "============================================================"
echo "  AI Survey Web Server"
echo "============================================================"
echo ""
echo "  Project: $PROJECT_DIR"
echo "  Port: $PORT"
echo ""

# Check if .env.local exists
if [ ! -f "$PROJECT_DIR/.env.local" ]; then
    echo "ERROR: .env.local not found in $PROJECT_DIR"
    echo "Please create .env.local with your LiveKit credentials"
    exit 1
fi

# Check if required packages are installed
echo "Checking dependencies..."
python3 -c "import fastapi, uvicorn, livekit" 2>/dev/null || {
    echo ""
    echo "Installing required packages..."
    pip install fastapi uvicorn python-dotenv livekit livekit-api jinja2 python-multipart
}

echo ""
echo "============================================================"
echo "  Starting server on http://localhost:$PORT"
echo "============================================================"
echo ""
echo "  Quick Start URL: http://localhost:$PORT/quick-start"
echo ""
echo "  For external access, run in another terminal:"
echo "    ngrok http $PORT"
echo ""
echo "============================================================"
echo ""

# Change to backend directory and start server
cd "$WEB_DIR/backend"
python3 server.py --port "$PORT"
