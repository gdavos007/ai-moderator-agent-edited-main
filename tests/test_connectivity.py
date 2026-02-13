"""
Test connectivity and configuration
"""
import pytest
import os
from pathlib import Path


def test_env_file_exists():
    """Test that .env.local file exists"""
    env_file = Path(__file__).parent.parent / ".env.local"
    assert env_file.exists(), ".env.local file not found. Please create it from .env.example"


def test_environment_variables():
    """Test that required environment variables are set"""
    from dotenv import load_dotenv
    load_dotenv(".env.local")

    required_vars = [
        "LIVEKIT_API_KEY",
        "LIVEKIT_API_SECRET",
        "LIVEKIT_URL",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
    ]

    missing = []
    for var in required_vars:
        value = os.getenv(var)
        if not value or value == f"your_{var.lower()}_here":
            missing.append(var)

    assert not missing, f"Missing or not configured: {', '.join(missing)}"


def test_config_validation():
    """Test configuration validation"""
    from dotenv import load_dotenv
    load_dotenv(".env.local")

    from config.agent_config import AgentConfig

    try:
        config = AgentConfig.from_env()
        config.validate()
        assert True
    except ValueError as e:
        pytest.fail(f"Configuration validation failed: {e}")


def test_livekit_url_format():
    """Test that LiveKit URL is properly formatted"""
    from dotenv import load_dotenv
    load_dotenv(".env.local")

    url = os.getenv("LIVEKIT_URL", "")
    assert url.startswith("ws://") or url.startswith("wss://"), \
        "LIVEKIT_URL must start with ws:// or wss://"


def test_imports():
    """Test that all modules can be imported"""
    try:
        from config.agent_config import AgentConfig, MODERATOR_INSTRUCTIONS
        from src.moderator_agent import CommunityModeratorAgent, create_moderator_session
        from src.utils import ParticipantTracker, ModerationLogger
        assert True
    except ImportError as e:
        pytest.fail(f"Import failed: {e}")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
