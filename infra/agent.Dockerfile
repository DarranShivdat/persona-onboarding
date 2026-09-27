# Agent service image (brain + text API + Pipecat voice). Build context: repo root.
# VOICE-001 pins versions and verifies the Silero/Smart-Turn model warmup at build time.
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends build-essential libopus0 ffmpeg && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY packages/flow /app/packages/flow
COPY services/agent /app/services/agent
RUN pip install --no-cache-dir "/app/services/agent[runtime,voice,observability]"
WORKDIR /app/services/agent
EXPOSE 8080
# Entrypoint module lands in VOICE-001 / FLOW-003.
CMD ["python", "-m", "agent.main", "--host", "0.0.0.0", "--port", "8080"]
