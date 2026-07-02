# LiveKit AI Moderator Agent
FROM python:3.12-slim
# Set working directory
WORKDIR /app
# Install system dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    && rm -rf /var/lib/apt/lists/*
# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Pre-download plugin model files (Silero VAD + turn-detector EOU model) so they
# ship in the image. Without this the turn detector fails at runtime with
# "Could not find file model_q8.onnx" (incident 2026-07).
RUN python -m livekit.agents download-files
# Copy ALL application files
COPY . .
# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app/src
# Run the agent with start command
CMD ["python", "agent.py", "start"]
