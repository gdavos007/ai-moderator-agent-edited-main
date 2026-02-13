#!/usr/bin/env python3
"""
Quick verification script - run this before installing dependencies
"""
import os
import sys

print("=" * 60)
print("AI Moderator Agent - Configuration Verification")
print("=" * 60)

# Check if .env.local exists
env_file = ".env.local"
if not os.path.exists(env_file):
    print(f"❌ {env_file} not found!")
    sys.exit(1)

print(f"✅ Found {env_file}")

# Read and check environment variables
with open(env_file, 'r') as f:
    content = f.read()

required_vars = {
    "LIVEKIT_URL": "LiveKit WebSocket URL",
    "LIVEKIT_API_KEY": "LiveKit API Key",
    "LIVEKIT_API_SECRET": "LiveKit API Secret",
    "OPENAI_API_KEY": "OpenAI API Key",
}

print("\n🔍 Checking configuration:")

all_valid = True
for var, description in required_vars.items():
    if var in content:
        # Extract the value
        for line in content.split('\n'):
            if line.startswith(var):
                value = line.split('=', 1)[1].strip()
                # Check if it's a placeholder
                if value and 'your_' not in value.lower() and len(value) > 5:
                    masked = value[:8] + "..." + value[-4:] if len(value) > 12 else "***"
                    print(f"   ✅ {description}: {masked}")
                else:
                    print(f"   ❌ {description}: Not configured (placeholder detected)")
                    all_valid = False
                break
    else:
        print(f"   ❌ {description}: Missing")
        all_valid = False

print("\n📋 Configuration Summary:")
print(f"   • Using OpenAI for: STT, LLM, TTS")
print(f"   • Model: gpt-4o-mini (balanced performance/cost)")
print(f"   • Anthropic/Claude: Not required")

print("\n" + "=" * 60)

if all_valid:
    print("✅ Configuration looks good!")
    print("\nNext steps:")
    print("1. Install uv: curl -LsSf https://astral.sh/uv/install.sh | sh")
    print("2. Install dependencies: uv sync")
    print("3. Install LiveKit CLI: brew install livekit-cli (macOS)")
    print("4. Download models: uv run agent.py download-files")
    print("5. Run agent: uv run agent.py dev")
    sys.exit(0)
else:
    print("⚠️  Configuration incomplete!")
    print("\nPlease update .env.local with your actual API keys:")
    print("   • LiveKit keys from: https://cloud.livekit.io")
    print("   • OpenAI key from: https://platform.openai.com/api-keys")
    sys.exit(1)
