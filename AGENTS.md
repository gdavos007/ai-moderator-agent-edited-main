# AGENTS.md

## Cursor Cloud specific instructions

### Overview
This is an AI-powered Voice Survey Moderator Agent built on the LiveKit Agents SDK. It conducts voice surveys by reading questions aloud and capturing participant responses via real-time WebRTC audio. The core stack is Python 3.12 + `uv` package manager.

### Services

| Service | Command | Notes |
|---------|---------|-------|
| **Agent Worker** | `uv run python agent.py dev` | Core process; connects to LiveKit Cloud. Requires valid `LIVEKIT_*` and `OPENAI_API_KEY` in `.env.local`. |
| **Web Join Portal** | `uv run uvicorn web.backend.server:app --port 8080` | Optional FastAPI UI for browser-based participant joining. |

### Key Development Notes

- **Package manager**: Use `uv sync --dev` (not `pip install`) to install dependencies. The lockfile is `uv.lock`.
- **Model files**: Run `uv run python agent.py download-files` once after install to download the turn-detector ONNX model from HuggingFace. Without this, the agent will fail with `Could not find file "model_q8.onnx"`.
- **Survey data**: The `src_data/` directory is gitignored. Sample JSON survey files must be created locally (see `surveys_config.yaml` for expected paths and formats).
- **Environment config**: Copy `.env.example` to `.env.local` and populate with real API keys. Required: `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`, `OPENAI_API_KEY`.
- **Google STT warning**: `Warning: Google STT plugin not available` is harmless — Google STT is an optional alternative provider.
- **Lint**: `uv run black --check .` and `uv run flake8 --max-line-length=120 --exclude=.venv,__pycache__ src/ config/ agent.py tests/`. The codebase has pre-existing lint issues (not formatted with black).
- **Tests**: `uv run pytest tests/ -v`. Two tests in `test_connectivity.py` will fail without real API keys (`test_environment_variables` checks `ANTHROPIC_API_KEY`, `test_imports` references a removed export). The remaining 81 tests are self-contained and should all pass.
- **No external databases/services**: All state is in-memory. No Docker, Redis, or database setup needed.
