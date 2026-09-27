"""STT / LLM / TTS factories for the call pipeline, chosen by `VoiceConfig` (env).

Adapted from Penciled voice-agent `services.py` (Darran's IP): Deepgram STT, Anthropic
LLM, Cartesia TTS with ServiceSwitcher failover to Deepgram TTS (reusing the Deepgram
key already required for STT, so the fallback is zero-config).

STT resilience (VOICE-001 decision): Deepgram only, relying on Pipecat's built-in
reconnect-with-backoff + KeepAlive on the Deepgram socket, plus a spoken filler and
polite end if STT becomes unusable (see `session.py`). No second STT vendor.
"""
from __future__ import annotations

from typing import Optional

from pipecat.processors.frame_processor import FrameProcessor

from .config import SAMPLE_RATE, VoiceConfig
from .failover import OnSwitched, build_tts_switcher
from .stub_llm import StubTurnLLM

VOICE_SYSTEM_PROMPT = (
    "You are Persona, a friendly personal AI assistant on a short onboarding phone call. "
    "Speak in one or two short, natural sentences. No lists, markdown, or emoji. "
    "If the caller wants to stop, thank them and say goodbye."
)


def build_stt(cfg: VoiceConfig) -> Optional[FrameProcessor]:
    if cfg.mode != "live":
        return None
    from pipecat.services.deepgram.stt import DeepgramSTTService

    return DeepgramSTTService(
        api_key=cfg.deepgram_key,
        sample_rate=SAMPLE_RATE,
        settings=DeepgramSTTService.Settings(model=cfg.stt_model, smart_format=True, interim_results=True),
    )


def build_llm(cfg: VoiceConfig) -> FrameProcessor:
    if not cfg.use_claude:
        return StubTurnLLM()
    from pipecat.services.anthropic.llm import AnthropicLLMService

    return AnthropicLLMService(
        api_key=cfg.anthropic_key,
        settings=AnthropicLLMService.Settings(
            model=cfg.llm_model, max_tokens=300, enable_prompt_caching=True,
            system_instruction=VOICE_SYSTEM_PROMPT,
        ),
        retry_timeout_secs=5.0,
        retry_on_timeout=True,  # ARCH §7: LLM timeout -> retry once (filler/template: VOICE-002)
    )


def build_tts_services(cfg: VoiceConfig) -> list[FrameProcessor]:
    """Primary first. Live: [Cartesia, Deepgram] (or [Deepgram] without a Cartesia key)."""
    if cfg.mode == "fake":
        from .fakes import ToneTTS

        return [ToneTTS(name="tone")]
    if cfg.mode != "live":
        return []
    from pipecat.services.deepgram.tts import DeepgramTTSService

    services: list[FrameProcessor] = []
    if cfg.cartesia_key:
        from pipecat.services.cartesia.tts import CartesiaTTSService

        services.append(CartesiaTTSService(
            api_key=cfg.cartesia_key,
            sample_rate=SAMPLE_RATE,
            settings=CartesiaTTSService.Settings(model=cfg.cartesia_model, voice=cfg.cartesia_voice),
        ))
    services.append(DeepgramTTSService(
        api_key=cfg.deepgram_key,
        sample_rate=SAMPLE_RATE,
        settings=DeepgramTTSService.Settings(voice=cfg.deepgram_tts_voice),
    ))
    return services


def build_tts(cfg: VoiceConfig, on_switched: Optional[OnSwitched] = None) -> FrameProcessor:
    return build_tts_switcher(build_tts_services(cfg), on_switched=on_switched)
