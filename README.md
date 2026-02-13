# AI Survey Moderator Agent

An AI-powered voice survey moderator built with LiveKit Agents SDK and OpenAI that conducts surveys by reading questions from Word documents and capturing participant responses via real-time voice conversation.

## 🎯 Core Features

### Survey Administration
- 📄 **Question Loading**: Reads survey questions from Word documents (`.docx`) with automatic parsing
- 🎙️ **Voice Interaction**: Natural conversation using OpenAI STT, TTS, and Silero VAD
- 👥 **Multi-Participant**: Supports multiple participants with round-robin question distribution
- 🎥 **Zoom Integration**: Join Zoom meetings as a bot via Recall.ai (Phase 1) 🆕
- ⏱️ **Time Management**: Configurable response time limits (default 20s) with grace periods
- 🔄 **Smart Polling**: Event-driven response capture with speech detection

### Response Capture & Processing
- 📝 **Real-time Transcription**: Captures participant responses using OpenAI STT
- ✅ **Response Correction**: Fuzzy matching to correct transcription errors against answer options
- 🔍 **Duplicate Prevention**: Ensures each response is captured only once
- 🎯 **Progress Tracking**: Monitors which participants have answered each question

### Data Export
- 📊 **CSV Export**: Structured data ready for analysis (Excel, Python, R)
- 📋 **JSON Transcript**: Complete conversation history for debugging
- ⏰ **Timestamps**: Every interaction timestamped for temporal analysis
- 🔒 **Session Isolation**: Each survey session creates separate output files

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     LiveKit Room                            │
│  ┌──────────────┐              ┌──────────────┐            │
│  │ Participant  │◄────────────►│  AI Agent    │            │
│  │   (Voice)    │   Audio      │   (Voice)    │            │
│  └──────────────┘   Tracks     └──────────────┘            │
└─────────────────────────────────────────────────────────────┘
                                        │
                    ┌───────────────────┼───────────────────┐
                    ▼                   ▼                   ▼
            ┌──────────────┐    ┌──────────────┐   ┌──────────────┐
            │  OpenAI STT  │    │  OpenAI LLM  │   │  OpenAI TTS  │
            │  (Transcribe)│    │  (Generate)  │   │   (Speak)    │
            └──────────────┘    └──────────────┘   └──────────────┘
                    │                                       
                    ▼                                       
        ┌──────────────────────┐                           
        │  Response Capture    │                           
        │  - Event Handler     │                           
        │  - Smart Polling     │                           
        │  - Correction        │                           
        └──────────────────────┘                           
                    │                                       
        ┌───────────┴───────────┐                          
        ▼                       ▼                           
┌──────────────┐        ┌──────────────┐                   
│   CSV Export │        │ JSON Transcript│                  
│ (Analysis)   │        │  (Debug)      │                   
└──────────────┘        └──────────────┘                   
```

## 📋 Requirements

- Python 3.12+
- LiveKit account and API credentials
- OpenAI API key
- macOS, Linux, or Windows

## 🚀 Setup

### 1. Clone the Repository

```bash
git clone https://github.com/YOUR_USERNAME/ai-moderator-agent.git
cd ai-moderator-agent
```

### 2. Create Virtual Environment

```bash
python3.12 -m venv ai_moderator_agent
source ai_moderator_agent/bin/activate  # On Windows: ai_moderator_agent\Scripts\activate
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure Environment

```bash
cp .env.example .env.local
```

Edit `.env.local` with your credentials:
```bash
# LiveKit Configuration
LIVEKIT_URL=wss://your-project.livekit.cloud
LIVEKIT_API_KEY=your_api_key
LIVEKIT_API_SECRET=your_api_secret

# OpenAI Configuration
OPENAI_API_KEY=your_openai_api_key
```

### 5. Add Survey Questions

Place your Word document (`.docx`) with survey questions in the `topic_questions/` directory.

**Question Format Example:**
```
Q#1: How would you rate our service?
excellent
good
fair
poor

Q#2: What can we improve?
[Open-ended question - no response options]
```

## 💻 Usage

### Option 1: LiveKit-Only Mode (Direct Participants)

### Start the Agent

**Terminal 1:**
```bash
source ai_moderator_agent/bin/activate
python agent.py dev
```

You should see:
```
Starting AI Survey Moderator Agent
registered worker
```

### Join Survey Session

**Terminal 2:**
```bash
source ai_moderator_agent/bin/activate
python join_survey.py
```

This will output a clickable link:
```
✨ CLICK THIS LINK to join (opens in browser):
https://meet.livekit.io/custom?liveKitUrl=...
```

Click the link to join via your web browser. Make sure to:
1. Grant microphone permissions
2. Unmute your microphone
3. Wait for the agent to start speaking

### Option 2: Zoom Integration (Phase 1: Recall.ai) 🆕

Conduct surveys with participants joining from Zoom meetings!

**Features**:
- ✅ Participants join via familiar Zoom interface
- ✅ Participant names automatically captured from Zoom
- ✅ No changes to existing agent code required
- ✅ Full survey functionality maintained

**Quick Start**:

```bash
# Terminal 1: Start webhook tunnel
ngrok http 8000

# Terminal 2: Start agent
python agent.py

# Terminal 3: Start Zoom bridge
python zoom_survey.py \
  --zoom-url "https://zoom.us/j/YOUR_MEETING_ID" \
  --webhook-url "https://YOUR_NGROK_URL.ngrok.io"
```

**Prerequisites**:
- Recall.ai API key (sign up at https://recall.ai/)
- ngrok installed (`brew install ngrok`)
- Add `RECALL_API_KEY` to `.env.local`

📖 **Complete Guide**: See [ZOOM_QUICKSTART.md](ZOOM_QUICKSTART.md) for 10-minute setup
📚 **Full Documentation**: See [ZOOM_SETUP.md](ZOOM_SETUP.md) for detailed guide, troubleshooting, and costs

## 📊 Output Files

Survey results are saved in `output/` directory:

### CSV Export
`AI_Survey_Agent_Output_Responses_YYYYMMDD_HHMMSS.csv`
```csv
Session ID,Participant Name,Question#,question_id,Question,Response Options,Participant Response,Timestamp
20251108_171604,John Doe,1,Q#1,How would you rate...?,excellent;good;fair;poor,Excellent,2025-11-08T17:17:03
```

### JSON Transcript
`survey_transcript_YYYYMMDD_HHMMSS.json`
```json
{
  "session_id": "20251108_171604",
  "start_time": "2025-11-08T17:16:04",
  "greeting": "Hello everyone! I'm your AI survey moderator...",
  "questions": [
    {
      "question_number": 1,
      "question_text": "How would you rate our service?",
      "responses": [
        {
          "participant": "John Doe",
          "response_text": "Excellent",
          "timestamp": "2025-11-08T17:17:03"
        }
      ]
    }
  ]
}
```

## ⚙️ Configuration

Key settings in `.env.local`:

| Variable | Description | Default |
|----------|-------------|---------|
| `LIVEKIT_URL` | LiveKit server WebSocket URL | Required |
| `LIVEKIT_API_KEY` | LiveKit API key | Required |
| `LIVEKIT_API_SECRET` | LiveKit API secret | Required |
| `OPENAI_API_KEY` | OpenAI API key | Required |
| `MAX_TURN_DURATION` | Max speaking time per participant (seconds) | 20 |
| `ENABLE_TURN_LIMITS` | Enable/disable time limits | True |
| `LOG_LEVEL` | Logging verbosity (DEBUG, INFO, WARNING) | INFO |

## 🛠️ Troubleshooting

### Agent Not Joining

Run cleanup script to kill processes and clear cache:
```bash
./cleanup.sh
```

Then restart:
```bash
python agent.py dev
```

### macOS BrokenPipeError

This is fixed in the code using `forkserver` multiprocessing context. If you still encounter issues, ensure you're running Python 3.12+.

### No Audio / Microphone Issues

1. Check browser microphone permissions
2. Ensure microphone is unmuted in browser
3. Wait for agent's "microphone ready" announcement
4. Check logs for `"source": "SOURCE_MICROPHONE"` (should not be `SOURCE_UNKNOWN`)

### Responses Not Captured

Check `logs/agent.log` for:
- `user_input_transcribed` events (should fire when you speak)
- `"source": "SOURCE_MICROPHONE"` in audio track logs
- STT transcript entries

## 📁 Project Structure

```
ai-moderator-agent/
├── agent.py                    # Main entry point
├── join_survey.py             # Session creation script (LiveKit only)
├── zoom_survey.py             # Zoom integration entry point (NEW)
├── cleanup.sh                 # Cleanup utility
├── config/
│   └── agent_config.py        # Configuration management
├── src/
│   ├── moderator_agent.py     # Core agent logic
│   ├── question_loader.py     # Word document parser
│   ├── participant_manager.py # Participant tracking
│   ├── survey_transcript.py   # JSON export
│   ├── survey_data_export.py  # CSV export
│   ├── audit_logger.py        # Audit logging
│   └── zoom_bridge/           # Zoom integration (NEW)
│       ├── recall_bot.py      # Recall.ai bot manager
│       ├── webhook_handler.py # Webhook event handler
│       └── audio_forwarder.py # Audio routing LiveKit↔Zoom
├── topic_questions/           # Survey questions (.docx files)
├── output/                    # Survey results (CSV + JSON)
└── logs/                      # Agent logs
```

## 🔧 Technical Highlights

- **Direct TTS Bypass**: Speaks questions verbatim to prevent AI hallucination
- **Event-Driven Capture**: Uses `user_input_transcribed` events for STT
- **Smart Polling**: Monitors speech state with 0.5s checks and 1s grace period
- **Forkserver Multiprocessing**: Eliminates macOS BrokenPipeError on Python 3.12+
- **Session Persistence**: `close_on_disconnect=False` keeps session alive
- **Fuzzy Matching**: Corrects transcription errors using edit distance

## 📝 License

MIT License - see LICENSE file for details

## 🤝 Contributing

This is a private repository. Contact the repository owner for collaboration access.

## 📧 Support

For issues or questions, please open an issue in the GitHub repository.
