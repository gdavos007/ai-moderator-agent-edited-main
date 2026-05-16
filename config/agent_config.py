"""
Agent configuration settings
"""
import os
from dataclasses import dataclass
from typing import Optional


@dataclass
class AgentConfig:
    """Configuration for the AI moderator agent"""

    # LiveKit settings
    livekit_api_key: str
    livekit_api_secret: str
    livekit_url: str

    # API keys
    openai_api_key: str
    deepgram_api_key: Optional[str] = ""  # Optional: only needed if using Deepgram
    anthropic_api_key: Optional[str] = ""  # Optional: only needed if using Claude
    eleven_api_key: Optional[str] = ""  # Optional: only needed if using ElevenLabs TTS

    # Agent identity
    agent_name: str = "CommunityModerator"
    agent_identity: str = "moderator-bot"

    # Provider Selection
    stt_provider: str = "openai"  # Options: "openai", "deepgram", or "google"
    tts_provider: str = "openai"  # Options: "openai", "deepgram", or "elevenlabs"

    # Model configuration
    # OpenAI STT options:
    #   - "gpt-4o-transcribe" (default): Newer model, generally more accurate, no prompt support
    #   - "whisper-1": Older model, supports prompt parameter for vocabulary guidance
    # Deepgram STT options:
    #   - "flux-general-en": Conversational model with built-in turn detection (English only)
    #   - "nova-3" (recommended): Latest model with keyterm prompting support
    #   - "nova-2": Previous generation, good accuracy
    #   - "nova": Previous generation
    #   - "whisper": Deepgram's Whisper implementation
    # Google Cloud STT options:
    #   - "latest_short" (recommended): Latest short-form model, optimized for latency
    #   - "latest_long": Latest long-form model, optimized for accuracy
    #   - "short": Short utterances model
    #   - "long": Longer audio model
    #   - "telephony": Telephony-optimized model
    #   - "telephony_short": Short telephony model
    #   Note: Requires GOOGLE_APPLICATION_CREDENTIALS env var pointing to service account JSON
    stt_model: str = "gpt-4o-transcribe"  # Default for OpenAI

    # LLM configuration (OpenAI or Anthropic)
    llm_model: str = "gpt-4o-mini"  # Using OpenAI for LLM

    # TTS configuration
    # OpenAI TTS options:
    #   - "gpt-4o-mini-tts": Fast, good quality
    #   - Voices: "ash", "ballad", "coral", "sage", "verse"
    # Deepgram TTS Aura 1 options:
    #   - Female: "aura-asteria-en", "aura-luna-en", "aura-stella-en", "aura-athena-en", "aura-hera-en"
    #   - Male: "aura-orion-en", "aura-arcas-en", "aura-perseus-en", "aura-angus-en", "aura-orpheus-en"
    # Deepgram TTS Aura 2 options (enterprise-grade, sub-200ms latency):
    #   - Female: "aura-2-thalia-en", "aura-2-andromeda-en", "aura-2-luna-en", "aura-2-stella-en"
    #   - Male: "aura-2-orpheus-en", "aura-2-arcas-en", "aura-2-orion-en"
    tts_model: str = "gpt-4o-mini-tts"  # Default for OpenAI
    tts_voice: str = "ash"  # Default for OpenAI

    # Agent behavior
    temperature: float = 0.0  # CRITICAL: Must be 0.0 to prevent LLM from creating its own questions
    log_level: str = "INFO"

    # Turn duration limits (in seconds) - SURVEY-OPTIMIZED GRACEFUL APPROACH
    # Timeline:
    #   1) 0 to max_turn_duration: Normal speaking
    #   2) max_turn_duration to +first_interrupt_grace: Silent grace period (allow completion)
    #   3) At +first_interrupt_grace: Give gentle warning + second_interrupt_grace more seconds
    #   4) At +first_interrupt_grace+second_interrupt_grace: Politely force end
    # Example: max=20s, first=10s, second=10s → 0-20s normal, 20-30s grace, 30s warn+10s, 40s end
    max_turn_duration: int = 20          # Base time limit (TESTING: 20s, PROD: 60s)
    turn_warning_duration: int = 15      # DEPRECATED - kept for compatibility only
    first_interrupt_grace: int = 10      # Silent grace period after base time (TESTING: 10s, PROD: 10s)
    second_interrupt_grace: int = 10     # Wrap-up time after warning (TESTING: 10s, PROD: 10s)
    enable_turn_limits: bool = True      # Feature toggle
    force_interrupt_enabled: bool = True # Enable time limit enforcement

    # ==================================================================================
    # ADAPTIVE SILENCE WAIT TIMES (in seconds)
    # ==================================================================================
    # These settings control how long the agent waits after a participant stops speaking
    # before considering their response complete. Longer waits allow participants to
    # pause and continue, but make the survey feel slower. Shorter waits feel more
    # responsive but may cut off participants who pause to think.
    #
    # Topic enforcement
    discussion_topic: str = "Global warming"  # Current discussion topic (TESTING: "Global warming")
    off_topic_interrupt_threshold: int = 15   # Interrupt after 15s of off-topic discussion
    enable_topic_enforcement: bool = True     # Feature toggle for topic enforcement

    # Room settings
    default_room_name: str = "community-discussion"
    max_participants: int = 50

    # VAD (Silero) configuration — overridable via env for tuning soft-spoken participants
    vad_activation_threshold: float = 0.5
    vad_min_speech_duration: float = 0.15
    vad_prefix_padding_duration: float = 0.6
    vad_min_silence_duration: float = 0.55

    @staticmethod
    def _extract_agent_name_from_voice(tts_voice: str, tts_provider: str) -> str:
        """
        Extract agent name from TTS voice setting.

        For Deepgram voices:
            - "aura-2-thalia-en" → "Thalia"
            - "aura-asteria-en" → "Asteria"
            - "aura-orion-en" → "Orion"

        For OpenAI voices:
            - "ash", "ballad", "coral", "sage", "verse" → Capitalize
        """
        if tts_provider == "deepgram":
            # Deepgram voice format: "aura-{name}-en" or "aura-2-{name}-en"
            # Extract the name part and capitalize it
            parts = tts_voice.replace("-en", "").split("-")
            # Find the name (not "aura" and not a number)
            for part in parts:
                if part != "aura" and not part.isdigit():
                    return part.capitalize()
            return "Moderator"  # Fallback
        else:
            # OpenAI voices: just capitalize
            return tts_voice.capitalize()

    @classmethod
    def from_env(cls) -> "AgentConfig":
        """Load configuration from environment variables"""
        # Get TTS settings first to derive agent name
        tts_provider = os.getenv("TTS_PROVIDER", "openai")
        tts_voice = os.getenv("TTS_VOICE", "ash")

        # Derive agent name from TTS voice (can be overridden by AGENT_NAME env var)
        derived_agent_name = cls._extract_agent_name_from_voice(tts_voice, tts_provider)
        agent_name = os.getenv("AGENT_NAME", "") or derived_agent_name

        return cls(
            livekit_api_key=os.getenv("LIVEKIT_API_KEY", ""),
            livekit_api_secret=os.getenv("LIVEKIT_API_SECRET", ""),
            livekit_url=os.getenv("LIVEKIT_URL", ""),
            openai_api_key=os.getenv("OPENAI_API_KEY", ""),
            deepgram_api_key=os.getenv("DEEPGRAM_API_KEY", ""),
            anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
            eleven_api_key=os.getenv("ELEVEN_API_KEY", ""),
            agent_name=agent_name,
            agent_identity=os.getenv("AGENT_IDENTITY", "moderator-bot"),
            # Provider selection
            stt_provider=os.getenv("STT_PROVIDER", "openai"),
            tts_provider=tts_provider,  # Already extracted above
            # Model selection (defaults depend on provider)
            stt_model=os.getenv("STT_MODEL", "gpt-4o-transcribe"),
            tts_model=os.getenv("TTS_MODEL", "gpt-4o-mini-tts"),
            tts_voice=tts_voice,  # Already extracted above
            log_level=os.getenv("LOG_LEVEL", "INFO"),
            temperature=float(os.getenv("TEMPERATURE", "0.0")),  # CRITICAL: 0.0 prevents question generation
            default_room_name=os.getenv("DEFAULT_ROOM_NAME", "community-discussion"),
            max_participants=int(os.getenv("MAX_PARTICIPANTS", "50")),
            # Turn duration settings
            max_turn_duration=int(os.getenv("MAX_TURN_DURATION", "20")),
            turn_warning_duration=int(os.getenv("TURN_WARNING_DURATION", "15")),
            first_interrupt_grace=int(os.getenv("FIRST_INTERRUPT_GRACE", "5")),
            second_interrupt_grace=int(os.getenv("SECOND_INTERRUPT_GRACE", "10")),
            enable_turn_limits=os.getenv("ENABLE_TURN_LIMITS", "true").lower() == "true",
            force_interrupt_enabled=os.getenv("FORCE_INTERRUPT_ENABLED", "true").lower() == "true",
            # Topic enforcement settings
            discussion_topic=os.getenv("DISCUSSION_TOPIC", "Global warming"),
            off_topic_interrupt_threshold=int(os.getenv("OFF_TOPIC_INTERRUPT_THRESHOLD", "15")),
            enable_topic_enforcement=os.getenv("ENABLE_TOPIC_ENFORCEMENT", "true").lower() == "true",
            # VAD tuning (Silero)
            vad_activation_threshold=float(os.getenv("VAD_ACTIVATION_THRESHOLD", "0.5")),
            vad_min_speech_duration=float(os.getenv("VAD_MIN_SPEECH_DURATION", "0.15")),
            vad_prefix_padding_duration=float(os.getenv("VAD_PREFIX_PADDING_DURATION", "0.6")),
            vad_min_silence_duration=float(os.getenv("VAD_MIN_SILENCE_DURATION", "0.55")),
        )

    def validate(self) -> bool:
        """Validate that required configuration is present"""
        required_fields = [
            ("LIVEKIT_API_KEY", self.livekit_api_key),
            ("LIVEKIT_API_SECRET", self.livekit_api_secret),
            ("LIVEKIT_URL", self.livekit_url),
        ]

        # Check provider-specific API keys
        if self.stt_provider == "openai" or self.tts_provider == "openai":
            required_fields.append(("OPENAI_API_KEY", self.openai_api_key))

        if self.stt_provider == "deepgram" or self.tts_provider == "deepgram":
            required_fields.append(("DEEPGRAM_API_KEY", self.deepgram_api_key))

        if self.tts_provider == "elevenlabs":
            required_fields.append(("ELEVEN_API_KEY", self.eleven_api_key))

        # Google Cloud uses GOOGLE_APPLICATION_CREDENTIALS env var (checked at runtime by SDK)
        if self.stt_provider == "google":
            google_creds = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "")
            if not google_creds:
                raise ValueError(
                    "GOOGLE_APPLICATION_CREDENTIALS environment variable is required when using Google STT.\n"
                    "Set it to the path of your Google Cloud service account JSON file."
                )

        missing = [name for name, value in required_fields if not value]

        if missing:
            raise ValueError(
                f"Missing required environment variables: {', '.join(missing)}\n"
                f"Please check your .env.local file"
            )

        # Validate provider values
        valid_stt_providers = ["openai", "deepgram", "google"]
        valid_tts_providers = ["openai", "deepgram", "elevenlabs"]
        if self.stt_provider not in valid_stt_providers:
            raise ValueError(f"Invalid STT_PROVIDER: {self.stt_provider}. Must be one of: {valid_stt_providers}")
        if self.tts_provider not in valid_tts_providers:
            raise ValueError(f"Invalid TTS_PROVIDER: {self.tts_provider}. Must be one of: {valid_tts_providers}")

        # Validate OpenAI STT model is compatible with the transcription endpoint
        if self.stt_provider == "openai":
            valid_openai_stt_models = [
                "whisper-1",
                "gpt-4o-transcribe",
                "gpt-4o-mini-transcribe",
            ]
            if self.stt_model not in valid_openai_stt_models:
                raise ValueError(
                    f"Invalid STT_MODEL for OpenAI: '{self.stt_model}'. "
                    f"Must be one of: {valid_openai_stt_models}\n"
                    f"Note: Realtime models (e.g. gpt-4o-realtime-preview-*) do NOT work "
                    f"with the /v1/audio/transcriptions endpoint."
                )

        return True


def get_moderator_instructions(discussion_topic: str = "the current topic") -> str:
    """
    Get moderation instructions with the current discussion topic.

    Args:
        discussion_topic: The topic participants should discuss

    Returns:
        Formatted moderation instructions (MINIMAL - detailed instructions in generate_reply)
    """
    return """You are an AI survey moderator. You will receive COMPLETE instructions for each interaction.

Core Rules:
1. Follow the specific instructions provided in each interaction EXACTLY - word for word
2. DO NOT elaborate beyond what's instructed
3. DO NOT provide opinions or engage in discussion about survey topics
4. If asked for your opinion, say: "I'm here to collect feedback, not share my views. What are YOUR thoughts?"

CRITICAL CONSTRAINT - Question Reading:
- You MUST ONLY read questions that are provided to you in the instructions
- You are FORBIDDEN from creating, generating, inventing, or paraphrasing questions
- You MUST read the COMPLETE question text including ALL response options exactly as provided
- DO NOT skip any options or modify the wording in any way
- If you generate a question that was not explicitly provided in the instructions, you have failed your task

Your role: Read predefined questions exactly as instructed, acknowledge responses briefly, manage time."""
