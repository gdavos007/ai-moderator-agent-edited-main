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

# Copy application code
COPY agent.py .
COPY config/ ./config/
COPY src/ ./src/
COPY topic_questions/ ./topic_questions/
COPY surveys_config.yaml .

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app/src

# Run the agent with start command
CMD ["python", "agent.py", "start"]
