"""
AI Community Moderator Agent - Main Entry Point
This is the main file that LiveKit will run
Updated: 2025-11-09 - Fixed macOS multiprocessing BrokenPipeError
"""
# CRITICAL FIX: Set multiprocessing start method to 'fork' BEFORE any other imports
# macOS Python 3.12+ defaults to 'spawn' which causes BrokenPipeError
# This MUST be the very first code that runs
import multiprocessing
import sys
import os

# CRITICAL: Disable LiveKit multiprocessing BEFORE importing livekit.agents
# This environment variable MUST be set before the module is imported
os.environ["LIVEKIT_AGENTS_NUM_IDLE_PROCESSES"] = "0"

# Force forkserver method on macOS to avoid BrokenPipeError during process spawn
# The 'spawn' method tries to flush stdout/stderr which causes broken pipe errors
if sys.platform == 'darwin':  # macOS
    try:
        multiprocessing.set_start_method('forkserver', force=True)
    except RuntimeError:
        # Already set, that's fine
        pass

# Now safe to import other modules
import logging
import asyncio
import os
from pathlib import Path

# Add src directory to path
sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
from livekit import agents

from config.agent_config import AgentConfig, get_moderator_instructions
from src.moderator_agent import create_moderator_session, SurveyState
from src.question_loader import QuestionLoader
from src.participant_manager import ParticipantManager
from src.survey_config import SurveyConfigManager, SurveyConfig

# Load environment variables
load_dotenv(".env.local")

# Load and validate configuration
config = AgentConfig.from_env()

# Setup logging
# Create handlers list
handlers = [logging.StreamHandler()]

# Only add file handler if logs directory exists (for local development)
logs_dir = Path("logs")
if logs_dir.exists():
    handlers.append(logging.FileHandler("logs/agent.log"))

logging.basicConfig(
    level=getattr(logging, config.log_level),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=handlers,
)
logger = logging.getLogger(__name__)

async def handle_welcome_section(session, question_loader, config):
    """
    Handle the welcome section from survey config or use defaults.

    Args:
        session: The agent session
        question_loader: QuestionLoader instance
        config: Agent configuration

    Returns:
        wait_seconds: Number of seconds to wait for audio setup
    """
    if question_loader.use_unified_format and question_loader.welcome_section and question_loader.welcome_section.enabled:
        # Use welcome section from survey config
        welcome = question_loader.welcome_section
        logger.info("Using welcome section from survey configuration")

        # Audio check message
        if welcome.audio_check and welcome.audio_check.enabled:
            audio_check_text = welcome.audio_check.message
            wait_seconds = welcome.audio_check.wait_seconds
        else:
            audio_check_text = "Please make sure your microphone is unmuted and working. I'll wait a moment for everyone to get ready."
            wait_seconds = 15

        # Get agent name: use survey-specific name if available, otherwise config default
        agent_name = question_loader.survey_meta.agent_name if question_loader.survey_meta.agent_name else config.agent_name
        logger.info(f"Using agent name for welcome: {agent_name}")

        # Greeting - substitute {agent_name} placeholder
        greeting_text = welcome.greeting.replace("{agent_name}", agent_name)

        # Instructions - also substitute {agent_name} in case it's used there
        instructions_text = welcome.instructions.replace("{agent_name}", agent_name)

        # Combine into single welcome message
        full_welcome = f"{greeting_text} {instructions_text} {audio_check_text}"

        logger.info(f"🎤 WELCOME TEXT SENT TO TTS: '{full_welcome}'")

        await session.say(full_welcome, allow_interruptions=False)
        logger.info("✅ Welcome TTS completed")
        return wait_seconds

    else:
        # Use default hardcoded welcome
        logger.info("Using default hardcoded welcome message")
        microphone_ready_text = f"Hello! I'm {config.agent_name}, your AI survey moderator. Please make sure your microphone is unmuted and working. I'll wait 1 minute for everyone to get ready before we begin."

        logger.info(f"🎤 WELCOME TEXT SENT TO TTS: '{microphone_ready_text}'")

        await session.say(microphone_ready_text, allow_interruptions=False)
        logger.info("✅ Welcome TTS completed")
        return 15


async def handle_initial_greeting(session, question_loader, config, participant_manager):
    """
    Handle the initial greeting after welcome.

    Args:
        session: The agent session
        question_loader: QuestionLoader instance
        config: Agent configuration
        participant_manager: ParticipantManager instance
    """
    if question_loader.use_unified_format:
        # Minimal greeting - welcome already handled
        participant_count = len(participant_manager.participants)
        if participant_count == 1:
            greeting_text = "Great! Thank you. We have our participant ready. Let's begin with our first question."
        else:
            greeting_text = f"Great! Thank you. We have {participant_count} participants ready. Let's begin with our first question."
    else:
        # Use default hardcoded greeting
        participant_count = len(participant_manager.participants) if participant_manager else 0
        greeting_text = f"Great! Thank you for joining today. I'll be asking a series of survey questions, and each of you will have about {config.max_turn_duration} seconds to respond. Let's keep our answers brief to ensure everyone has a turn. Let's get started! Please wait for me to call on you for your response."

    await session.say(greeting_text, allow_interruptions=False)

    # Record greeting in survey transcript and data export
    moderator = session._agent
    moderator.survey_transcript.add_greeting(greeting_text)
    moderator.survey_data_export.set_greeting(greeting_text)


async def _upload_transcript_on_shutdown(moderator) -> None:
    """POST the final survey transcript to the Post-Session Report Portal.

    Registered via ctx.add_shutdown_callback so it runs AFTER ctx.room.disconnect()
    — it cannot block avatar cleanup or entrypoint completion. Exceptions are
    logged and swallowed; failure is never fatal.
    """
    upload_url = os.environ.get("REPORT_UPLOAD_URL")
    upload_secret = os.environ.get("REPORT_UPLOAD_SECRET")
    if not (upload_url and upload_secret):
        logger.info("📤 Skipping transcript upload: REPORT_UPLOAD_URL/SECRET not set")
        return
    try:
        import httpx
        transcript_data = moderator.survey_transcript.transcript
        session_id = transcript_data.get("session_id") or ""
        if not session_id:
            logger.warning("📤 Skipping upload: survey_transcript has no session_id")
            return

        questions = []
        try:
            for q in (moderator.question_loader.questions or []):
                questions.append({
                    "id": getattr(q, "id", ""),
                    "question": getattr(q, "question", ""),
                    "type": getattr(q, "question_type", ""),
                    "response_options": getattr(q, "response_options", None) or [],
                })
        except Exception as qe:
            logger.warning(f"[report-upload] Could not serialize questions: {qe}")

        payload = {
            "session_id": session_id,
            "title": (getattr(moderator.survey_config, "name", None)
                      or getattr(moderator.survey_config, "survey_id", "Focus Group")),
            "room_name": moderator.ctx.room.name if moderator.ctx and moderator.ctx.room else "",
            "started_at": transcript_data.get("session_start"),
            "ended_at": transcript_data.get("session_end"),
            "participants": transcript_data.get("participants", []),
            "transcript": transcript_data,
            "questions": questions,
        }

        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(
                f"{upload_url.rstrip('/')}/api/sessions/{session_id}/transcript",
                json=payload,
                headers={"X-Upload-Secret": upload_secret},
            )
        if 200 <= r.status_code < 300:
            logger.info(
                f"📤 Transcript uploaded for session {session_id} "
                f"(HTTP {r.status_code})"
            )
        else:
            logger.warning(
                f"📤 Transcript upload returned HTTP {r.status_code}: "
                f"{r.text[:300]}"
            )
    except Exception as e:
        logger.warning(f"📤 Transcript upload failed (non-fatal): {e}")


async def request_handler(job_request: agents.JobRequest):
    """
    Custom request handler to log job details before acceptance.
    With agent_name="survey-moderator", only explicit dispatches are accepted.
    """
    # Log job request details for debugging
    logger.info(f"📨 RECEIVED job request: {job_request.job.id}")
    logger.info(f"   Room: {job_request.job.room}")
    logger.info(f"   Agent Name: {job_request.job.agent_name}")
    logger.info(f"   PID: {multiprocessing.current_process().pid}")

    # Accept the job
    logger.info(f"✅ ACCEPTING job {job_request.job.id}")
    await job_request.accept()


async def entrypoint(ctx: agents.JobContext):
    """
    Main entrypoint for the LiveKit agent.

    This function is called when the agent joins a room.

    Args:
        ctx: Job context provided by LiveKit
    """
    try:
        logger.info("=" * 80)
        logger.info(f"🚨 NEW JOB RECEIVED - Agent starting")
        logger.info(f"Room: {ctx.room.name}")
        logger.info(f"Job ID: {ctx.job.id}")
        logger.info(f"Job Agent Name: {ctx.job.agent_name}")
        logger.info(f"Process PID: {multiprocessing.current_process().pid}")
        logger.info("=" * 80)

        # ── Duplicate agent guard ────────────────────────────────────────────
        # If another survey-moderator agent is already in the room (e.g. due
        # to a dispatch race), exit immediately before welcome TTS / avatar.
        from livekit.rtc import ParticipantKind
        for participant in ctx.room.remote_participants.values():
            is_agent = (
                getattr(participant, "kind", None) == ParticipantKind.PARTICIPANT_KIND_AGENT
                or participant.identity.startswith("agent")
            )
            if is_agent and participant.identity != ctx.room.local_participant.identity:
                logger.warning(
                    f"⚠️ DUPLICATE AGENT DETECTED — another agent already in room "
                    f"{ctx.room.name}: {participant.identity} (kind={getattr(participant, 'kind', '?')}). "
                    f"Exiting this job ({ctx.job.id}) to avoid double welcome/avatar."
                )
                return  # Exit entrypoint — no welcome, no avatar, no survey

        # Validate configuration
        config.validate()

        # Load survey configuration
        try:
            survey_manager = SurveyConfigManager("surveys_config.yaml")
            if not survey_manager.load_config():
                raise RuntimeError("Failed to load survey configuration")

            # Get survey (respects SURVEY_ID env var override)
            survey_config = survey_manager.get_survey()
            logger.info(f"Selected survey: {survey_config.name} (ID: {survey_config.survey_id})")

            # Initialize question loader with specific survey file
            question_loader = QuestionLoader(questions_file=str(survey_config.questions_file))

        except FileNotFoundError:
            logger.warning("surveys_config.yaml not found, using legacy auto-detection")
            question_loader = QuestionLoader(questions_dir="topic_questions")
            survey_config = None

        # Get moderation instructions
        moderator_instructions = get_moderator_instructions(config.discussion_topic)

        # Initialize participant manager
        participant_manager = ParticipantManager()

        # Load questions
        if not question_loader.load_questions():
            logger.error("Failed to load questions!")
            raise RuntimeError("Could not load survey questions")

        progress = question_loader.get_progress()
        logger.info(f"Loaded {progress['total_questions']} questions for survey")

        # Create the moderator session with question-based moderation
        session = await create_moderator_session(
            ctx=ctx,
            instructions=moderator_instructions,
            stt_provider=config.stt_provider,
            tts_provider=config.tts_provider,
            stt_model=config.stt_model,
            llm_model=config.llm_model,
            tts_model=config.tts_model,
            tts_voice=config.tts_voice,
            deepgram_api_key=config.deepgram_api_key,
            eleven_api_key=config.eleven_api_key,
            temperature=config.temperature,
            max_turn_duration=config.max_turn_duration,
            turn_warning_duration=config.turn_warning_duration,
            first_interrupt_grace=config.first_interrupt_grace,
            second_interrupt_grace=config.second_interrupt_grace,
            enable_turn_limits=config.enable_turn_limits,
            force_interrupt_enabled=config.force_interrupt_enabled,
            discussion_topic=config.discussion_topic,
            off_topic_interrupt_threshold=config.off_topic_interrupt_threshold,
            enable_topic_enforcement=False,  # Disabled for survey mode - only time limits matter
            vad_activation_threshold=config.vad_activation_threshold,
            vad_min_speech_duration=config.vad_min_speech_duration,
            vad_prefix_padding_duration=config.vad_prefix_padding_duration,
            vad_min_silence_duration=config.vad_min_silence_duration,
            question_loader=question_loader,
            participant_manager=participant_manager,
            survey_config=survey_config,
        )

        logger.info("Agent is now active and monitoring the discussion")

        # Get moderator reference
        moderator = session._agent

        # Register transcript upload as a shutdown callback. Runs AFTER
        # ctx.room.disconnect() inside the LiveKit job shutdown sequence, so a
        # slow or unreachable backend cannot block avatar teardown.
        ctx.add_shutdown_callback(lambda: _upload_transcript_on_shutdown(moderator))
        logger.info("📤 Registered transcript upload shutdown callback")

        # Get expected participant count and observer mode from room metadata FIRST
        expected_participants = None
        has_observer = False
        try:
            import json
            if ctx.room.metadata:
                room_metadata = json.loads(ctx.room.metadata)
                expected_participants = room_metadata.get("expected_participants")
                has_observer = room_metadata.get("has_observer", False)
                logger.info(f"Room metadata: expecting {expected_participants} participant(s), observer mode: {has_observer}")
        except Exception as e:
            logger.warning(f"Could not parse room metadata: {e}")
            expected_participants = None
            has_observer = False

        # Configure observer mode if enabled
        if has_observer:
            moderator.observer_mode_enabled = True
            moderator.survey_state = SurveyState.WAITING_FOR_OBSERVER
            logger.info("👁️ OBSERVER MODE ENABLED - Agent will stay SILENT until observer says 'start survey'")

        # Add existing participants to the participant manager
        # (participants who joined before agent started; exclude agents e.g. Anam avatar)
        from livekit.rtc import ParticipantKind
        for participant in ctx.room.remote_participants.values():
            if getattr(participant, "kind", None) == ParticipantKind.PARTICIPANT_KIND_AGENT:
                logger.info(f"Skipping agent participant (existing): {participant.identity}")
                continue
            if participant.identity.startswith("agent") or participant.identity == "anam-avatar-agent":
                logger.info(f"Skipping agent participant by identity (existing): {participant.identity}")
                continue
            logger.info(f"Found existing participant: {participant.identity} (name: {participant.name})")
            if moderator.participant_manager:
                moderator.participant_manager.add_participant(participant.identity, display_name=participant.name)
            moderator.participant_audio_activity[participant.identity] = None

        # ========== OBSERVER MODE: Wait silently for "start survey" ==========
        if has_observer:
            logger.info("👁️ Observer mode: Agent staying SILENT - waiting for observer's 'start survey' command")

            # Switch STT to listen to observer so we can hear their voice commands
            observer_identity = moderator.participant_manager.get_observer_identity()
            if observer_identity:
                try:
                    audio_input = session._room_io._audio_input
                    audio_input.set_participant(observer_identity)
                    logger.info(f"👁️ STT switched to listen to observer: {observer_identity}")
                except Exception as e:
                    logger.warning(f"Could not switch STT to observer: {e}")
            else:
                logger.warning("👁️ Observer not yet connected - will detect when they join")

            logger.info("👁️ Agent is now SILENTLY waiting for observer 'start survey' command")
            # The observer command will be detected in the STT event handler
            # When observer says "start survey", handle_observer_command will be called
            # which will then trigger the full survey flow via start_survey_flow()

            # Store references needed for when observer starts the survey
            moderator.pending_survey_start = {
                'session': session,
                'question_loader': question_loader,
                'config': config,
                'ctx': ctx
            }

            # Don't do anything else - just wait for observer
            # Handle room events for participant tracking
            @ctx.room.on("participant_connected")
            def on_participant_connected(participant):
                logger.info(f"Participant joined: {participant.identity}")
                # Add to participant manager
                if moderator.participant_manager:
                    moderator.participant_manager.add_participant(participant.identity, display_name=participant.name)
                # If this is the observer and we haven't set STT yet, do it now
                if moderator.participant_manager.is_observer(participant.identity):
                    try:
                        audio_input = session._room_io._audio_input
                        audio_input.set_participant(participant.identity)
                        logger.info(f"👁️ STT switched to listen to newly joined observer: {participant.identity}")
                    except Exception as e:
                        logger.warning(f"Could not switch STT to observer: {e}")

            @ctx.room.on("participant_disconnected")
            def on_participant_disconnected(participant):
                logger.info(f"Participant left: {participant.identity}")

            @ctx.room.on("track_published")
            def on_track_published(publication, participant):
                logger.info(
                    f"Track published by {participant.identity}: "
                    f"{publication.kind} - {publication.source}"
                )

            return  # Exit here - survey will be started by observer command

        # ========== NORMAL MODE: Standard survey flow ==========

        # If the avatar is configured but is still starting up (lazy-start
        # triggered by participant_connected), wait for it to reach CONNECTED
        # and then allow its video pipeline a short warm-up before delivering
        # the welcome. Otherwise the first sentence plays before Anam's
        # lip-sync pipeline has primed, causing the voice/avatar desync.
        if os.environ.get("ANAM_AVATAR_ID") and not moderator._audio_only_mode:
            AVATAR_WAIT_TIMEOUT = 30.0  # Anam cold-start can exceed 10s on slow links
            AVATAR_WARMUP_GRACE = 2.0   # let video pipeline stabilize post-CONNECT

            was_already_connected = moderator._avatar_connected
            wait_start = asyncio.get_event_loop().time()
            deadline = wait_start + AVATAR_WAIT_TIMEOUT
            while asyncio.get_event_loop().time() < deadline:
                if moderator._avatar_connected or moderator._audio_only_mode:
                    break
                await asyncio.sleep(0.25)
            waited_for = asyncio.get_event_loop().time() - wait_start

            if moderator._avatar_connected and not was_already_connected:
                logger.info(
                    f"AVATAR_LIFECYCLE avatar CONNECTED after {waited_for:.1f}s — "
                    f"applying {AVATAR_WARMUP_GRACE}s warm-up grace before welcome TTS"
                )
                await asyncio.sleep(AVATAR_WARMUP_GRACE)
            elif moderator._avatar_connected:
                logger.info("AVATAR_LIFECYCLE avatar was already CONNECTED before welcome — proceeding in sync")
            elif moderator._audio_only_mode:
                logger.info("AVATAR_LIFECYCLE downgraded to audio-only during wait — proceeding")
            else:
                logger.warning(
                    f"AVATAR_LIFECYCLE avatar not CONNECTED after {waited_for:.1f}s wait "
                    f"(state={getattr(moderator, '_avatar_state', '?')}) — proceeding anyway, "
                    f"welcome may desync"
                )

        # WELCOME SECTION
        # Use welcome from survey config or default
        logger.info("Delivering welcome message...")
        wait_seconds = await handle_welcome_section(session, question_loader, config)

        # Smart waiting for participants - monitor for new joiners
        logger.info(f"Waiting {wait_seconds} seconds for participants to fully connect and unmute...")
        logger.info("This gives participants time to:")
        logger.info("  1. Join the room")
        logger.info("  2. Grant microphone permissions")
        logger.info("  3. Unmute their microphone")
        logger.info("  4. Establish audio connection with LiveKit")

        # Wait for specified duration
        initial_participant_count = len(moderator.participant_manager.participants) if moderator.participant_manager else 0
        logger.info(f"Initial participant count: {initial_participant_count}")
        if expected_participants:
            logger.info(f"Expecting {expected_participants} total participant(s)")
        await asyncio.sleep(wait_seconds)

        # Check for new participants after waiting (exclude agents e.g. Anam avatar)
        for participant in ctx.room.remote_participants.values():
            if getattr(participant, "kind", None) == ParticipantKind.PARTICIPANT_KIND_AGENT:
                continue
            if participant.identity.startswith("agent") or participant.identity == "anam-avatar-agent":
                continue
            if moderator.participant_manager and participant.identity not in moderator.participant_manager.participants:
                logger.info(f"Adding participant who joined during wait: {participant.identity} (name: {participant.name})")
                moderator.participant_manager.add_participant(participant.identity, display_name=participant.name)
                moderator.participant_audio_activity[participant.identity] = None

        current_participant_count = len(moderator.participant_manager.participants) if moderator.participant_manager else 0
        logger.info(f"After {wait_seconds} seconds: {current_participant_count} participant(s) present")

        # Log final participant status
        final_count = len(moderator.participant_manager.participants) if moderator.participant_manager else 0
        if expected_participants:
            logger.info(f"Wait complete. Participant count: {final_count}/{expected_participants}")
        else:
            logger.info(f"Wait complete. Participant count: {final_count}")

        # MULTI-PARTICIPANT FIX: Subscribe to all participant audio tracks
        from livekit.rtc import TrackKind
        logger.info("Subscribing to participant audio tracks...")
        for participant in ctx.room.remote_participants.values():
            # Get ALL tracks, not just audio, to see what's available
            all_tracks = list(participant.track_publications.values())
            audio_tracks = [track for track in all_tracks if track.kind == TrackKind.KIND_AUDIO]

            logger.info(f"📊 {participant.identity}: {len(all_tracks)} total tracks, {len(audio_tracks)} audio")

            if audio_tracks:
                for track_pub in audio_tracks:
                    logger.info(f"  Track: {track_pub.sid}, subscribed={track_pub.subscribed}, kind={track_pub.kind}")
                    if not track_pub.subscribed:
                        logger.info(f"📡 Subscribing to audio track from {participant.identity}")
                        track_pub.set_subscribed(True)
                        logger.info(f"✅ Subscription set for {participant.identity}")
                logger.info(f"✅ {participant.identity}: {len(audio_tracks)} audio track(s) processed")
            else:
                logger.warning(f"⚠️  {participant.identity}: NO audio tracks found yet")
                logger.info(f"     This is normal if microphone permission not granted yet")
                logger.info(f"     Tracks will be subscribed via track_published event when ready")

        # INITIAL GREETING
        logger.info("Delivering initial greeting...")
        await handle_initial_greeting(session, question_loader, config, moderator.participant_manager)

        # Wait a moment after greeting, then start asking questions
        await asyncio.sleep(3)

        # Start asking questions — transition OUT of WELCOME phase
        if hasattr(moderator, 'ask_next_question'):
            from src.moderator_agent import SurveyState
            moderator.survey_state = SurveyState.RUNNING
            logger.info(f"🟢 Survey state: WELCOME → RUNNING (welcome/greeting complete, questions starting)")

            # #region agent log
            try:
                import json as _json
                with open("/Users/ganeshkrishnan/Documents/Lever_AI_FINAL/ai-moderator-agent-edited-main/.cursor/debug.log", "a") as _f:
                    _f.write(_json.dumps({"location": "agent.py:state_transition", "message": "WELCOME → RUNNING transition", "data": {"survey_state": moderator.survey_state.value}, "timestamp": int(__import__('datetime').datetime.now().timestamp() * 1000), "hypothesisId": "B"}) + "\n")
            except Exception:
                pass  # Best-effort — path doesn't exist in Docker
            # #endregion

            # Clear any response fragments that may have accumulated during welcome
            moderator.response_fragments = []
            moderator.latest_user_response = None
            moderator.pending_stt_transcript = None
            moderator.last_stt_fragment = ""
            moderator.response_captured = False
            logger.info("🧹 Cleared response buffers before first question")

            logger.info("Starting question-based survey")
            await moderator.ask_next_question()
        else:
            logger.warning("Question-based moderation not available on this agent")

        # Handle room events
        @ctx.room.on("participant_connected")
        def on_participant_connected(participant):
            logger.info(f"Participant joined: {participant.identity}")

        @ctx.room.on("participant_disconnected")
        def on_participant_disconnected(participant):
            logger.info(f"Participant left: {participant.identity}")

        @ctx.room.on("track_published")
        def on_track_published(publication, participant):
            logger.info(
                f"Track published by {participant.identity}: "
                f"{publication.kind} - {publication.source}"
            )

    except Exception as e:
        logger.error(f"Error in agent entrypoint: {e}", exc_info=True)
        raise


async def prewarm(proc: agents.JobProcess):
    """Prewarm function to initialize resources"""
    logger.info("Prewarming agent process...")
    # The fork start method eliminates BrokenPipeError, no special handling needed
    # This ensures the agent is ready to accept jobs immediately


if __name__ == "__main__":
    # Run the agent
    logger.info("Starting AI Survey Moderator Agent")
    logger.info(f"Agent Name: {config.agent_name}")
    logger.info(f"Agent Identity: {config.agent_identity}")
    logger.info(f"LiveKit URL: {config.livekit_url}")
    logger.info(f"Max Turn Duration: {config.max_turn_duration}s")
    logger.info(f"Turn Limits Enabled: {config.enable_turn_limits}")

    # Log STT/TTS provider configuration (redact sensitive keys)
    openai_base = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1 (default)")
    openai_key_hint = config.openai_api_key[:8] + "..." if config.openai_api_key else "(not set)"
    logger.info(f"STT Provider: {config.stt_provider} | STT Model: {config.stt_model}")
    logger.info(f"TTS Provider: {config.tts_provider} | TTS Model: {config.tts_model} | Voice: {config.tts_voice}")
    logger.info(f"OpenAI Base URL: {openai_base}")
    logger.info(f"OpenAI API Key: {openai_key_hint}")

    # Worker configuration - multiprocessing disabled via environment variables at top of file
    from livekit.agents import JobExecutorType

    worker_options = agents.WorkerOptions(
        entrypoint_fnc=entrypoint,
        request_fnc=request_handler,  # Custom handler for logging
        job_executor_type=JobExecutorType.THREAD,  # Use threads, not processes
        num_idle_processes=0,  # No idle processes
        max_retry=0,  # No retries
        agent_name="survey-moderator",  # Only accept explicit dispatches with this name
    )

    agents.cli.run_app(worker_options)
