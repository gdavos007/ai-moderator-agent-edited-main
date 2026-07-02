# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AI-powered voice survey moderator built on the **LiveKit Agents SDK**. The agent joins LiveKit rooms, reads survey questions aloud via TTS, captures participant responses via STT, and exports results to CSV/JSON. Supports multi-participant round-robin and a web-based join experience for participants. An optional **Anam talking-head avatar** renders the agent's voice as video.

**Pipeline (as deployed in production):** Deepgram `nova-3` STT → OpenAI LLM (temperature 0.0) → ElevenLabs TTS, with Silero VAD and LiveKit `noise_cancellation.BVC()`. The code *defaults* to OpenAI STT/TTS (see `config/agent_config.py`), but the LiveKit Cloud deployment overrides these to Deepgram + ElevenLabs via secrets (`STT_PROVIDER`, `TTS_PROVIDER`, etc.).

> ⚠️ Zoom integration via Recall.ai is **not functional**. The web-based join experience is the primary and working delivery method.

> ℹ️ Noise cancellation is **`BVC()`**, not `NC()`. `NC()` was tried but crashes the agent process (`std::terminate` on a mic sample-rate mismatch) the moment a participant mic connects. Do not swap back to `NC()` without resolving that. (BVC is single-speaker-tuned and can attenuate quieter participants — a known tradeoff.)

## How to Run (Development)

Three terminals are required to run the full stack locally:

```bash
# Terminal 1 — Agent
PYTHONPATH=src uv run python agent.py dev

# Terminal 2 — Web API / Frontend Server
PYTHONPATH=src uv run python web/backend/server.py

# Terminal 3 — Public URL for client access
ngrok http 8080
```

Share the **ngrok URL** with the client to join and run the survey from their browser.

## How to Deploy (Production)

There are **two separately-deployed runtimes**:

| Runtime | Where | How to deploy | What it does |
|---------|-------|---------------|--------------|
| **Agent** | LiveKit Cloud | `lk agent deploy` | Joins rooms, runs the STT/LLM/TTS pipeline, drives the Anam avatar |
| **Web frontend** | Hetzner VPS (`root@5.78.145.255`) | `git pull` + `docker build`/`docker run` (port 8083→8080) | Browser join UI for participants |

```bash
# Agent → LiveKit Cloud (from repo root; agent id read from livekit.toml)
lk agent deploy
lk agent status          # Sleeping = scaled to zero (normal); wakes on a session
lk agent logs            # streams NEW lines only — start it BEFORE reproducing an issue

# Agent secrets/env live in LiveKit Cloud, NOT in the deployed image.
# Editing .env.local does NOT affect the deployed agent. Change via the LiveKit
# dashboard (single value) or:  lk agent update-secrets --secrets KEY=VALUE --overwrite
# ⚠️ update-secrets does a FULL REPLACE — passing one key wipes the rest. Prefer the dashboard.
```

The Anam avatar is selected by the `ANAM_AVATAR_ID` secret. It must be an **avatar id** (see `https://api.anam.ai/v1/avatars`), NOT a **persona id** — passing a persona id fails to render, and since the agent's voice routes through the avatar, the room goes silent. `avatarModel` is pinned to `cara-3` in code (the org lacks access to `cara-4-latest`).

## Other Common Commands

```bash
# Start the agent (production / Docker)
python agent.py start

# Start a fresh survey session with N participants
python3 start_fresh_survey.py --participants 3

# Run all tests
python -m pytest tests/

# Run a single test
python -m pytest tests/test_repeat_request_heuristic.py -v

# Select a specific survey at runtime
SURVEY_ID=quantitative_example python agent.py dev

# Install dependencies (uses uv with pyproject.toml)
uv sync
# Or with pip
pip install -r requirements.txt
```

## Architecture

### Entry Point Flow
`agent.py` → registers a LiveKit worker with `agent_name="survey-moderator"` (explicit dispatch only). On job acceptance, it:
1. Loads survey config from `surveys_config.yaml` via `SurveyConfigManager`
2. Loads questions via `QuestionLoader` (supports JSON unified format and legacy `.docx`)
3. Creates `AgentSession` with STT/LLM/TTS/VAD pipeline via `create_moderator_session()`
4. Delivers welcome message, waits for participants, then calls `ask_next_question()`

### Core Module: `src/moderator_agent.py` (~5500 lines)
**This is the primary file for all agent logic.** Contains:
- **`CommunityModeratorAgent(Agent)`** — the LiveKit Agent subclass with all survey logic
- **`create_moderator_session()`** — factory that wires up STT, LLM, TTS, VAD providers
- **`SurveyState` enum** — WELCOME → WAITING_FOR_OBSERVER → RUNNING → PAUSED → COMPLETED
- Response capture via `user_input_transcribed` events with fuzzy matching (`correct_transcription()`)
- Multi-participant round-robin question delivery
- Observer mode (moderator stays silent until observer says "start survey")
- Turn time management with grace periods and interruption

### Supporting Modules (`src/`)
| Module | Purpose |
|--------|---------|
| `question_loader.py` | Loads questions from `.docx` or JSON; `QuestionLoader` class |
| `question_loader_json.py` | JSON-specific loader (`JSONQuestionLoader`) for unified format |
| `participant_manager.py` | Tracks participants, round-robin ordering, observer detection |
| `survey_data_export.py` | CSV export of responses |
| `survey_transcript.py` | JSON transcript export |
| `survey_config.py` | `SurveyConfig` / `SurveyConfigManager` — loads `surveys_config.yaml` |
| `audit_logger.py` | Audit logging |
| `stt_debug_logger.py` | STT debug output (JSONL files in `output/`) |
| `keyword_extractor.py` | Extracts keywords for STT prompting |
| `zoom_bridge/` | Recall.ai Zoom integration — **NOT functional, do not modify** |

### Domain Modules (`src/domain/`)
Pure logic extracted from `moderator_agent.py` — this is what the `tests/` mirror.

| Module | Purpose |
|--------|---------|
| `constants.py` | Tunable thresholds: `PAUSE_COOLDOWN_*`, `STABILIZATION_*`, `POLL_WAIT_CAP_*`, `MIN_COMMITTED_CHARS`, watchdog timeouts, avatar states |
| `turn_phase.py` | `TurnPhase` enum + allowed-transition table (IDLE → MODERATOR_SPEAKING → AWAITING_RESPONSE → …) |
| `turn_state.py` | Per-turn state (`TurnInfo`, `TurnResult`) |
| `deadline_manager.py` | `DeadlineManager` — polling deadlines, epoch bumping, STT/idle watchdogs. **Governs STT-failure nudges, NOT normal ack timing.** |
| `response_analysis.py` | `analyze_response()` — LLM relevance / repeat-request / partial-answer / already-answered classification |
| `transcription.py` | `correct_transcription()` fuzzy option matching + `parse_multi_option_response()` |
| `text_analysis.py` | Cheap heuristics: `_is_committable`, `_is_disfluent_starter`, `_is_too_short_for_offtopic`, uncertain/greeting detection |
| `delivery_state.py` | Question-delivery confirmation guards |
| `observer.py` | Observer-mode detection |

### Response-Timing / Latency (in `_await_response` + `_process_captured_response`)
The time from a participant stopping speaking to "Thank you, {name}" is governed by:
- **Gates in `_await_response`** — pause-cooldown + stabilization, branched on `current_question_object.is_quantitative()` vs `is_qualitative()` (constants in `constants.py`). Quantitative gates are short; qualitative are longer to avoid clipping open-ended answers.
- **Ack-before-analysis (Priority 1)** — `_fire_early_ack()` speaks the ack concurrently with the LLM analysis (which then runs for corrective follow-up only), removing ~1s from time-to-ack. `_ack_already_spoken` coordinates so retries and `move_to_next_participant()` don't double-ack.
- **Diagnostics** — `_log_ack_latency_metrics()` emits `📊 METRIC ack_latency[...]` per ack (grep the agent logs for `ack_latency`).

Do **not** tune `deadline_manager.py` for ack latency — it only affects STT-failure nudges.

### Configuration
- **`config/agent_config.py`** — `AgentConfig` dataclass loaded from env vars. STT providers (OpenAI/Deepgram/Google), TTS providers (OpenAI/Deepgram/ElevenLabs), configurable turn timing, and **env-tunable Silero VAD** (`VAD_ACTIVATION_THRESHOLD`, `VAD_MIN_SILENCE_DURATION`, `VAD_MIN_SPEECH_DURATION`, `VAD_PREFIX_PADDING_DURATION`). Dataclass defaults are OpenAI; production overrides to Deepgram + ElevenLabs via secrets.
- **`.env.local`** — LOCAL DEV credentials only (LIVEKIT_URL, LIVEKIT_API_KEY/SECRET, OPENAI_API_KEY, DEEPGRAM_API_KEY, ELEVEN_API_KEY, ANAM_API_KEY, ANAM_AVATAR_ID, etc.). Editing this does **not** change the deployed agent — LiveKit Cloud secrets do (see *How to Deploy*).
- **`surveys_config.yaml`** — survey definitions with `survey_id`, `questions_file` path, and enable/disable flags. Default is `poc_focus_group` (all-qualitative). Override with `SURVEY_ID`. (Note: some example paths in this file — `quantitative_example`, `mixed_example` — point to files that are not currently on disk.)
- **`livekit.toml`** — LiveKit cloud project subdomain + agent ID (used by `lk agent` commands)

### Web Frontend (`web/`)
FastAPI server (`web/backend/server.py`) with Jinja2 templates providing a browser-based join UI for participants. Runs separately from the agent on port 8080.

## Key Design Decisions

- **Temperature 0.0** for LLM — prevents the agent from inventing questions; it must only read provided questions verbatim
- **macOS multiprocessing fix** — `agent.py` sets `forkserver` start method and `LIVEKIT_AGENTS_NUM_IDLE_PROCESSES=0` before any LiveKit imports to avoid `BrokenPipeError`
- **Thread executor** — uses `JobExecutorType.THREAD` instead of process-based execution
- **Fuzzy STT correction** — `correct_transcription()` uses edit distance, phonetic matching, and common misrecognition mappings to match responses to expected answer options
- **Uncertain response nudging** — phrases like "I don't know", "pass", "skip" trigger an encouragement prompt asking the participant to try answering
- **Avatar carries the voice** — when the Anam avatar is active, the agent's TTS is published *through* the avatar. A broken/misconfigured avatar (bad `ANAM_AVATAR_ID`, model access 403) therefore silences the agent, not just the video. The code downgrades to audio-only on a clean avatar-start failure, but a bad avatar id can still cause silence.

## Logs & Output

### `logs/` folder
- **`agent.log`** — raw transcript of all demo sessions; primary debugging reference
- **`audit_{YYYYMMDD}_{XXXXXX}.json`** — per-session audio records capturing who said what and when

### `output/` folder
- `AI_Survey_Agent_Output_Responses_*.csv` — structured response data
- `AI_Survey_Agent_Output_Metadata_*.csv` — session metadata
- `survey_transcript_*.json` — full conversation transcript
- `stt_debug_*.jsonl` / `stt_comparison_*.txt` — STT debugging

## Tests

Tests in `tests/` are unit tests that mirror logic from `moderator_agent.py` rather than importing it directly (due to heavy LiveKit dependencies). When changing core logic like phrase lists or heuristics in the source, corresponding test files must be updated manually. In particular, tests pin constant values (e.g. `test_substance_gate.py` asserts exact `PAUSE_COOLDOWN_*` / `STABILIZATION_*` / `POLL_WAIT_CAP_*`), so **update those assertions in the same commit** when you change a constant.

> ℹ️ Importing anything under `src/` transitively imports `moderator_agent.py`, which imports `livekit.plugins.google`. If that plugin isn't installed locally, `src`-importing tests fail at collection (`ImportError: cannot import name 'google'`). Pure-logic tests that don't import `src` (e.g. `test_stale_fragment_analysis.py`) still run. Full test runs happen in an environment with all plugins installed.