#!/usr/bin/env python3
"""
Test LiveKit connectivity and configuration
"""
import sys
import os
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv


def test_environment_variables():
    """Test that all environment variables are set"""
    print("🔍 Checking environment variables...")

    load_dotenv(".env.local")

    required_vars = {
        "LIVEKIT_API_KEY": "LiveKit API Key",
        "LIVEKIT_API_SECRET": "LiveKit API Secret",
        "LIVEKIT_URL": "LiveKit WebSocket URL",
        "OPENAI_API_KEY": "OpenAI API Key",
        "ANTHROPIC_API_KEY": "Anthropic API Key",
    }

    all_valid = True
    for var, description in required_vars.items():
        value = os.getenv(var, "")
        if not value or "your_" in value.lower():
            print(f"   ❌ {description} ({var}) - Not configured")
            all_valid = False
        else:
            # Mask the key for security
            masked = value[:8] + "..." + value[-4:] if len(value) > 12 else "***"
            print(f"   ✅ {description} ({var}) - {masked}")

    return all_valid


def test_configuration():
    """Test configuration loading and validation"""
    print("\n🔍 Testing configuration...")

    try:
        from config.agent_config import AgentConfig

        config = AgentConfig.from_env()
        config.validate()

        print(f"   ✅ Configuration valid")
        print(f"   • Agent Name: {config.agent_name}")
        print(f"   • Agent Identity: {config.agent_identity}")
        print(f"   • STT Model: {config.stt_model}")
        print(f"   • LLM Model: {config.llm_model}")
        print(f"   • TTS Model: {config.tts_model}")
        print(f"   • LiveKit URL: {config.livekit_url}")

        return True
    except Exception as e:
        print(f"   ❌ Configuration error: {e}")
        return False


def test_imports():
    """Test that all modules can be imported"""
    print("\n🔍 Testing module imports...")

    try:
        from config.agent_config import AgentConfig, MODERATOR_INSTRUCTIONS
        print("   ✅ config.agent_config")

        from src.moderator_agent import CommunityModeratorAgent, create_moderator_session
        print("   ✅ src.moderator_agent")

        from src.utils import ParticipantTracker, ModerationLogger
        print("   ✅ src.utils")

        return True
    except Exception as e:
        print(f"   ❌ Import error: {e}")
        return False


def test_livekit_connection():
    """Test basic LiveKit connection"""
    print("\n🔍 Testing LiveKit connection...")

    try:
        from livekit import api
        import asyncio

        load_dotenv(".env.local")

        async def check_connection():
            livekit_api = api.LiveKitAPI(
                os.getenv("LIVEKIT_URL"),
                os.getenv("LIVEKIT_API_KEY"),
                os.getenv("LIVEKIT_API_SECRET"),
            )

            # Try to list rooms (this will verify connectivity)
            try:
                rooms = await livekit_api.room.list_rooms()
                return True, len(rooms)
            except Exception as e:
                return False, str(e)

        success, result = asyncio.run(check_connection())

        if success:
            print(f"   ✅ Successfully connected to LiveKit")
            print(f"   • Current rooms: {result}")
            return True
        else:
            print(f"   ❌ Connection failed: {result}")
            return False

    except Exception as e:
        print(f"   ❌ Connection test error: {e}")
        return False


def main():
    """Run all tests"""
    print("=" * 60)
    print("AI Moderator Agent - Connectivity Test")
    print("=" * 60)

    results = []

    # Test 1: Environment variables
    results.append(("Environment Variables", test_environment_variables()))

    # Test 2: Configuration
    results.append(("Configuration", test_configuration()))

    # Test 3: Imports
    results.append(("Module Imports", test_imports()))

    # Test 4: LiveKit connection
    results.append(("LiveKit Connection", test_livekit_connection()))

    # Summary
    print("\n" + "=" * 60)
    print("Test Summary")
    print("=" * 60)

    all_passed = True
    for test_name, passed in results:
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{status} - {test_name}")
        if not passed:
            all_passed = False

    print("=" * 60)

    if all_passed:
        print("\n🎉 All tests passed! Your agent is ready to run.")
        print("\nTo start the agent in development mode:")
        print("   uv run agent.py dev")
        print("\nTo test in the playground:")
        print("   1. Visit your LiveKit Cloud dashboard")
        print("   2. Go to Agents Playground")
        print("   3. Start a session")
        return 0
    else:
        print("\n⚠️  Some tests failed. Please fix the issues above.")
        print("\nCommon fixes:")
        print("   • Update .env.local with your API keys")
        print("   • Run: lk cloud auth")
        print("   • Verify your LiveKit project is active")
        return 1


if __name__ == "__main__":
    sys.exit(main())
