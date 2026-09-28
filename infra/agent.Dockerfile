# Agent service image (brain + text API + Pipecat voice). Build context: REPO ROOT.
#   Fly builds it remotely:  fly deploy --remote-only -c infra/fly.toml .   (scripts/deploy/fly-agent.sh)
#   Local (if Docker exists): docker build -f infra/agent.Dockerfile -t persona-agent .
# Deps: exactly the pyproject `dependencies` + extras runtime,voice,observability, pinned by
# infra/agent-constraints.txt. The agent package itself is NOT pip-installed: it runs from
# /app/services/agent so agent/brain/spec.py finds /app/packages/flow/flow.yaml (REPO_ROOT).

# ---- build: venv with pinned deps ----------------------------------------------------------
FROM python:3.12-slim AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*
RUN python -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH
COPY services/agent/pyproject.toml /tmp/pyproject.toml
COPY infra/agent-constraints.txt /tmp/constraints.txt
RUN python -c "import tomllib; p = tomllib.load(open('/tmp/pyproject.toml', 'rb'))['project']; x = p['optional-dependencies']; print('\n'.join(p['dependencies'] + x['runtime'] + x['voice'] + x['observability']))" > /tmp/requirements.txt \
    && cat /tmp/requirements.txt
# CPU-only torch first (the default PyPI wheel drags in ~3 GB of CUDA libs).
RUN pip install --index-url https://download.pytorch.org/whl/cpu "torch==2.9.1" "torchaudio==2.9.1"
RUN pip install -r /tmp/requirements.txt -c /tmp/constraints.txt

# ---- runtime ------------------------------------------------------------------------------
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:$PATH \
    PYTHONPATH=/app/services/agent \
    NLTK_DATA=/usr/local/share/nltk_data \
    HOME=/home/persona
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 --shell /usr/sbin/nologin persona
COPY --from=build /opt/venv /opt/venv
WORKDIR /app
COPY packages/flow /app/packages/flow
COPY infra/supabase/migrations /app/infra/supabase/migrations
COPY services/agent/agent /app/services/agent/agent
# Build-time smoke: fail the (remote) build, not the first call, on a broken dep set.
# Also pre-fetch the NLTK tokenizer so the non-root runtime never downloads at call time.
RUN python -c "import nltk; nltk.download('punkt_tab', download_dir='/usr/local/share/nltk_data', quiet=True)" \
    && python -c "import agent.api.app, agent.voice.config, agent.voice.ice, pipecat.audio.vad.silero, pipecat.audio.turn.smart_turn.local_smart_turn_v3" \
    && chown -R persona:persona /app
USER persona
WORKDIR /app/services/agent
EXPOSE 8080
# agent/main.py is VOICE-005's entrypoint (text API + voice on one port).
CMD ["python", "-m", "agent.main", "--host", "0.0.0.0", "--port", "8080"]
