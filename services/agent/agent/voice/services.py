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

# GUARD-001: the voice LLM only says (a shortened) brain line or fills record_slots args.
# 120 tokens covers a two-sentence line or one record_slots call; low temperature keeps it
# on the line instead of improvising.
# HOTFIX 2026-09-28: 120 truncated the record_slots tool call (its JSON args alone run
# ~100-200 tokens), so the call never reached the handler and every caller turn fell
# through to the silence floor ("Are you still there?"). The cap must fit a full
# extraction call, same as the text extractor; spoken length is bounded by the prompt and
# the speech guard (MAX_SENTENCES), not by max_tokens.
VOICE_LLM_MAX_TOKENS = 512
VOICE_LLM_TEMPERATURE = 0.3

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


EXTRACTION_TOOL = "record_slots"


def force_extraction(params: dict, tool: str = EXTRACTION_TOOL) -> dict:
    """LAT-003 (Penciled comparison): on a node whose only job for the LLM is extraction
    (direct speech: the brain's line is spoken by TTS, never by the model), force the tool
    call. The model then skips its "speak or call?" decision and any preamble: ~150-250 ms
    less time to the complete tool call (bench: docs/penciled-comparison.md). Nodes with no
    extraction tool (terminal) are left alone: forcing a missing tool is an API error."""
    tools = params.get("tools") or []
    names = [t.get("name") for t in tools if isinstance(t, dict)]
    if tool in names:
        params["tool_choice"] = {"type": "tool", "name": tool}
    return params


def build_llm(cfg: VoiceConfig) -> FrameProcessor:
    if not cfg.use_claude:
        return StubTurnLLM()
    from pipecat.adapters.services.anthropic_adapter import AnthropicLLMAdapter
    from pipecat.services.anthropic.llm import AnthropicLLMService

    cls = AnthropicLLMService
    if cfg.direct_speech and cfg.force_extraction:
        class _ForcedExtractionAdapter(AnthropicLLMAdapter):
            def get_llm_invocation_params(self, context, enable_prompt_caching, system_instruction=None):
                params = super().get_llm_invocation_params(context, enable_prompt_caching, system_instruction)
                return force_extraction(params)  # type: ignore[arg-type]

        class _ForcedExtractionLLM(AnthropicLLMService):
            adapter_class = _ForcedExtractionAdapter

        cls = _ForcedExtractionLLM

    return cls(
        api_key=cfg.anthropic_key,
        settings=AnthropicLLMService.Settings(
            model=cfg.llm_model, max_tokens=VOICE_LLM_MAX_TOKENS, temperature=VOICE_LLM_TEMPERATURE,
            enable_prompt_caching=True,
            system_instruction=VOICE_SYSTEM_PROMPT,
        ),
        retry_timeout_secs=5.0,
        retry_on_timeout=True,  # ARCH §7: LLM timeout -> retry once; Flows role/task messages override the prompt
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
