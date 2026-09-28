"""Process-boot prewarm for the call pipeline (LAT-003, adapted from Penciled `warmup.py`).

Runs once in the background when the API starts with live voice (never blocks /health or
the first request). Best-effort and time-boxed; a failure only logs.

  1. Imports the pipeline modules (Pipecat, aiortc, Deepgram/Cartesia/Anthropic services:
     ~0.8 s the first call after a deploy would otherwise pay) and loads the Silero VAD and
     Smart Turn v3 ONNX models once (file cache + onnxruntime init), with a dummy inference.
  2. Mints the Cloudflare TURN credentials into the ICE cache (ice.py), so the first call's
     `GET /ice` and server leg skip that round trip.

Per call, `CallSession` also warms its own Anthropic client's connection while the greeting
plays (`prewarm_llm_connection`), so the first extraction skips the TCP/TLS handshake.
"""
from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger

STEP_TIMEOUT_S = 15.0
_WARM: list[Any] = []   # keep loaded models alive for the life of the process


def _warm_models(cfg) -> None:
    import numpy as np

    from . import session  # noqa: F401  (imports the whole pipeline stack)

    params = session.build_user_params(cfg)
    vad = params.vad_analyzer
    try:
        vad.set_sample_rate(16000)
        vad.voice_confidence(b"\x00\x00" * vad.num_frames_required())
    except Exception as e:  # noqa: BLE001 - construction already did the heavy load
        logger.debug(f"warmup: VAD dummy inference skipped ({e})")
    for strat in getattr(getattr(params, "user_turn_strategies", None), "stop", None) or []:
        turn = getattr(strat, "_turn_analyzer", None) or getattr(strat, "turn_analyzer", None)
        predict = getattr(turn, "_predict_endpoint", None)
        if predict is not None:
            try:
                predict(np.zeros(16000, dtype=np.float32))
            except Exception as e:  # noqa: BLE001
                logger.debug(f"warmup: Smart Turn dummy inference skipped ({e})")
    _WARM.append(params)


async def _warm_ice() -> None:
    from .ice import resolve_ice

    await resolve_ice()


async def run_warmup(cfg) -> None:
    """Never raises."""
    steps = [("models", lambda: asyncio.to_thread(_warm_models, cfg)), ("ice", _warm_ice)]
    for name, fn in steps:
        try:
            await asyncio.wait_for(fn(), timeout=STEP_TIMEOUT_S)
            logger.info(f"voice warmup: {name} ready")
        except Exception as e:  # noqa: BLE001 - best effort
            logger.warning(f"voice warmup step {name!r} failed: {type(e).__name__}: {e}")


async def prewarm_llm_connection(llm: Any) -> None:
    """Open the call's Anthropic HTTP connection now (a free models list), not on the first
    caller turn. No-op for the stub LLM or a client without the call."""
    client = getattr(llm, "_client", None)
    models = getattr(client, "models", None)
    if models is None or not hasattr(models, "list"):
        return
    try:
        await asyncio.wait_for(models.list(limit=1), timeout=5.0)
    except Exception as e:  # noqa: BLE001 - the first turn just pays the handshake
        logger.debug(f"LLM connection prewarm skipped: {type(e).__name__}")
