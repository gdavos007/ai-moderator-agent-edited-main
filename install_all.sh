#!/bin/bash
# Complete installation script for AI Moderator Agent
# This will install all required dependencies

set -e  # Exit on error

echo "============================================================"
echo "AI Moderator Agent - Complete Installation"
echo "============================================================"
echo ""

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Step 1: Check for Homebrew
echo "Step 1: Checking for Homebrew..."
if command -v brew &> /dev/null; then
    echo -e "${GREEN}✅ Homebrew already installed: $(brew --version | head -1)${NC}"
else
    echo -e "${YELLOW}⚠️  Homebrew not found. Installing...${NC}"
    echo ""
    echo "This will install Homebrew (the macOS package manager)."
    echo "You may be prompted for your password."
    echo ""

    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

    # Add Homebrew to PATH
    if [[ $(uname -m) == 'arm64' ]]; then
        # Apple Silicon
        echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> ~/.zprofile
        eval "$(/opt/homebrew/bin/brew shellenv)"
    else
        # Intel
        echo 'eval "$(/usr/local/bin/brew shellenv)"' >> ~/.zprofile
        eval "$(/usr/local/bin/brew shellenv)"
    fi

    echo -e "${GREEN}✅ Homebrew installed successfully${NC}"
fi

echo ""

# Step 2: Install system dependencies
echo "Step 2: Installing system dependencies (pkg-config, ffmpeg)..."
if brew list pkg-config &> /dev/null; then
    echo -e "${GREEN}✅ pkg-config already installed${NC}"
else
    brew install pkg-config
    echo -e "${GREEN}✅ pkg-config installed${NC}"
fi

if brew list ffmpeg &> /dev/null; then
    echo -e "${GREEN}✅ ffmpeg already installed${NC}"
else
    brew install ffmpeg
    echo -e "${GREEN}✅ ffmpeg installed${NC}"
fi

echo ""

# Step 3: Install LiveKit CLI
echo "Step 3: Installing LiveKit CLI..."
if command -v lk &> /dev/null; then
    echo -e "${GREEN}✅ LiveKit CLI already installed: $(lk version 2>&1 | head -1 || echo 'installed')${NC}"
else
    brew install livekit-cli
    echo -e "${GREEN}✅ LiveKit CLI installed${NC}"
fi

echo ""

# Step 4: Activate Python environment and install dependencies
echo "Step 4: Installing Python dependencies..."

# Check if virtual environment exists
if [ ! -d "ai_moderator_agent" ]; then
    echo -e "${YELLOW}Creating virtual environment...${NC}"
    python3 -m venv ai_moderator_agent
fi

# Activate environment
source ai_moderator_agent/bin/activate

# Upgrade pip
echo "Upgrading pip..."
pip install --upgrade pip setuptools wheel -q

# Install dependencies
echo "Installing LiveKit Agents and plugins..."
pip install -r requirements.txt

echo -e "${GREEN}✅ Python dependencies installed${NC}"

echo ""

# Step 5: Download model files
echo "Step 5: Downloading model files..."
echo "This may take a few minutes..."

python agent.py download-files

echo -e "${GREEN}✅ Model files downloaded${NC}"

echo ""

# Step 6: Verify setup
echo "Step 6: Verifying configuration..."
python verify_setup.py

echo ""
echo "============================================================"
echo -e "${GREEN}🎉 Installation Complete!${NC}"
echo "============================================================"
echo ""
echo "Next steps:"
echo ""
echo "1. Authenticate with LiveKit Cloud:"
echo "   ${YELLOW}lk cloud auth${NC}"
echo ""
echo "2. Activate the environment (do this each time you work):"
echo "   ${YELLOW}source ai_moderator_agent/bin/activate${NC}"
echo ""
echo "3. Run the agent:"
echo "   ${YELLOW}python agent.py dev${NC}"
echo ""
echo "4. Test in playground:"
echo "   ${YELLOW}https://cloud.livekit.io${NC} → Agents → Playground"
echo ""
echo "============================================================"
