# INFRA-001 voice spike image (echo bot only; no vendor keys). Build context: repo root.
#   docker build -f infra/voice-spike/spike.Dockerfile -t persona-voice-spike .
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
# aiortc/av/pylibsrtp ship manylinux wheels with their native libs bundled.
WORKDIR /app/services/agent
# Pinned to the version the local proof ran on (pipecat-ai 1.4.0 / aiortc 1.14.0).
RUN pip install --no-cache-dir "pipecat-ai[webrtc]==1.4.0" "fastapi>=0.115" "uvicorn[standard]>=0.30" "httpx>=0.27" "loguru>=0.7"
COPY services/agent/agent /app/services/agent/agent
EXPOSE 8080
CMD ["python", "-m", "agent.voice.spike_echo", "--host", "0.0.0.0", "--port", "8080"]
