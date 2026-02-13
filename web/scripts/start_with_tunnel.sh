#!/bin/bash
# ============================================================
# Start Survey Web Server with ngrok Tunnel
# ============================================================
#
# This script starts both the web server AND ngrok tunnel,
# giving you a public URL to share with participants.
#
# Prerequisites:
#   - ngrok installed (brew install ngrok)
#   - ngrok authenticated (ngrok config add-authtoken YOUR_TOKEN)
#
# Usage:
#   ./start_with_tunnel.sh
#
# ============================================================

set -e

# Get script directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
WEB_DIR="$( cd "$SCRIPT_DIR/.." && pwd )"
PROJECT_DIR="$( cd "$WEB_DIR/.." && pwd )"

PORT=8080

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo ""
echo -e "${BLUE}============================================================${NC}"
echo -e "${BLUE}  AI Survey - Web Server + Tunnel${NC}"
echo -e "${BLUE}============================================================${NC}"
echo ""

# Check if ngrok is installed
if ! command -v ngrok &> /dev/null; then
    echo -e "${RED}ERROR: ngrok is not installed${NC}"
    echo ""
    echo "Install ngrok:"
    echo "  macOS: brew install ngrok"
    echo "  Or download from: https://ngrok.com/download"
    echo ""
    echo "Then authenticate:"
    echo "  ngrok config add-authtoken YOUR_TOKEN"
    echo ""
    exit 1
fi

# Check if .env.local exists
if [ ! -f "$PROJECT_DIR/.env.local" ]; then
    echo -e "${RED}ERROR: .env.local not found${NC}"
    exit 1
fi

# Check dependencies
echo "Checking dependencies..."
python3 -c "import fastapi, uvicorn, livekit" 2>/dev/null || {
    echo "Installing required packages..."
    pip install fastapi uvicorn python-dotenv livekit livekit-api jinja2 python-multipart
}

# Function to cleanup on exit
cleanup() {
    echo ""
    echo -e "${YELLOW}Shutting down...${NC}"
    kill $SERVER_PID 2>/dev/null || true
    kill $NGROK_PID 2>/dev/null || true
    exit 0
}

trap cleanup SIGINT SIGTERM

# Start web server in background
echo ""
echo -e "${GREEN}Starting web server on port $PORT...${NC}"
cd "$WEB_DIR/backend"
python3 server.py --port $PORT &
SERVER_PID=$!

# Wait for server to start
sleep 2

# Check if server started successfully
if ! kill -0 $SERVER_PID 2>/dev/null; then
    echo -e "${RED}ERROR: Web server failed to start${NC}"
    exit 1
fi

echo -e "${GREEN}Web server started (PID: $SERVER_PID)${NC}"

# Start ngrok
echo ""
echo -e "${GREEN}Starting ngrok tunnel...${NC}"
ngrok http $PORT --log=stdout > /tmp/ngrok.log 2>&1 &
NGROK_PID=$!

# Wait for ngrok to establish tunnel
sleep 3

# Get the public URL from ngrok API
NGROK_URL=$(curl -s http://localhost:4040/api/tunnels 2>/dev/null | python3 -c "import sys, json; data = json.load(sys.stdin); print(data['tunnels'][0]['public_url'] if data.get('tunnels') else '')" 2>/dev/null || echo "")

if [ -z "$NGROK_URL" ]; then
    echo -e "${YELLOW}Could not get ngrok URL automatically.${NC}"
    echo "Check http://localhost:4040 for the tunnel URL"
    NGROK_URL="(check http://localhost:4040)"
fi

echo ""
echo -e "${BLUE}============================================================${NC}"
echo -e "${GREEN}  SERVER READY!${NC}"
echo -e "${BLUE}============================================================${NC}"
echo ""
echo -e "  ${YELLOW}Share this URL with participants:${NC}"
echo ""
echo -e "    ${GREEN}${NGROK_URL}/quick-start${NC}"
echo ""
echo -e "${BLUE}============================================================${NC}"
echo ""
echo "  Local URL:     http://localhost:$PORT"
echo "  ngrok Dashboard: http://localhost:4040"
echo ""
echo -e "  ${YELLOW}Make sure agent.py is running in another terminal:${NC}"
echo "    SURVEY_ID=your_survey python agent.py dev"
echo ""
echo -e "${BLUE}============================================================${NC}"
echo ""
echo "Press Ctrl+C to stop both server and tunnel"
echo ""

# Wait for processes
wait $SERVER_PID
