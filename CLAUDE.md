# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AI-powered voice survey moderator built on **LiveKit Agents SDK** and **OpenAI**. The agent joins LiveKit rooms, reads survey questions aloud via TTS, captures participant responses via STT, and exports results to CSV/JSON. Supports multi-participant round-robin and a web-based join experience for participants.

> ⚠️ Zoom integration via Recall.ai is **not functional**. The web-based join experience is the primary and working delivery method.

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

### Core Module: `src/moderator_agent.py` (~5900 lines)
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

### Configuration
- **`config/agent_config.py`** — `AgentConfig` dataclass loaded from env vars. Supports multiple STT providers (OpenAI, Deepgram, Google), TTS providers (OpenAI, Deepgram, ElevenLabs), and configurable turn timing.
- **`.env.local`** — credentials (LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET, OPENAI_API_KEY, etc.)
- **`surveys_config.yaml`** — survey definitions with `survey_id`, `questions_file` path, and enable/disable flags
- **`livekit.toml`** — LiveKit cloud project/agent ID

### Web Frontend (`web/`)
FastAPI server (`web/backend/server.py`) with Jinja2 templates providing a browser-based join UI for participants. Runs separately from the agent on port 8080.

## Key Design Decisions

- **Temperature 0.0** for LLM — prevents the agent from inventing questions; it must only read provided questions verbatim
- **macOS multiprocessing fix** — `agent.py` sets `forkserver` start method and `LIVEKIT_AGENTS_NUM_IDLE_PROCESSES=0` before any LiveKit imports to avoid `BrokenPipeError`
- **Thread executor** — uses `JobExecutorType.THREAD` instead of process-based execution
- **Fuzzy STT correction** — `correct_transcription()` uses edit distance, phonetic matching, and common misrecognition mappings to match responses to expected answer options
- **Uncertain response nudging** — phrases like "I don't know", "pass", "skip" trigger an encouragement prompt asking the participant to try answering

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

Tests in `tests/` are unit tests that mirror logic from `moderator_agent.py` rather than importing it directly (due to heavy LiveKit dependencies). When changing core logic like phrase lists or heuristics in the source, corresponding test files must be updated manually.