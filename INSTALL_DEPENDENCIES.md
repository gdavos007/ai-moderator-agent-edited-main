# Installing System Dependencies

Your Python environment is ready, but we need to install some system-level dependencies first.

## Issue

PyAV (required for LiveKit audio/video processing) needs `pkg-config` to build.

## Solution

### Option 1: Install Homebrew (Recommended) ⭐

Homebrew is the package manager for macOS. If you don't have it:

```bash
# Install Homebrew
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# Follow the on-screen instructions to add Homebrew to your PATH
# Usually something like:
echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> ~/.zprofile
eval "$(/opt/homebrew/bin/brew shellenv)"

# Install pkg-config and ffmpeg
brew install pkg-config ffmpeg

# Verify installation
pkg-config --version
```

### Option 2: Use Pre-built Wheels (Faster Alternative)

If you don't want to install Homebrew, you can use pre-compiled wheels:

```bash
# Activate your environment
source ai_moderator_agent/bin/activate

# Install with pre-built wheels
pip install --only-binary :all: av

# Then install the rest
pip install "livekit-agents[silero,turn-detector]~=1.2" \
            "livekit-plugins-noise-cancellation~=0.2" \
            "livekit-agents[openai]~=1.2" \
            python-dotenv
```

## After Installing Dependencies

Once pkg-config is installed, run:

```bash
# Activate environment
source ai_moderator_agent/bin/activate

# Install all dependencies
pip install "livekit-agents[silero,turn-detector]~=1.2" \
            "livekit-plugins-noise-cancellation~=0.2" \
            "livekit-agents[openai]~=1.2" \
            python-dotenv

# Download model files
python agent.py download-files

# Test configuration
python scripts/test_connection.py

# Run the agent
python agent.py dev
```

## Quick Commands Reference

```bash
# Activate environment (do this every time you work on the project)
source ai_moderator_agent/bin/activate

# Deactivate when done
deactivate

# Install dependencies (after activating)
pip install -r requirements.txt  # We'll create this

# Run agent
python agent.py dev
```
