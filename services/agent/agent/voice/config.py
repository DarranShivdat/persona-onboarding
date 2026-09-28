"""Voice pipeline configuration from env (names only — values never live in the repo).

    DEEPGRAM_API_KEY         STT (required for a real call) + fallback TTS
    DEEPGRAM_STT_MODEL       default nova-3
    DEEPGRAM_TTS_VOICE       fallback Aura voice, default aura-2-thalia-en
    CARTESIA_API_KEY         primary TTS (optional: without it Deepgram TTS is primary)
    CARTESIA_VOICE_ID        Cartesia voice id
    CARTESIA_MODEL           default sonic-2
    ANTHROPIC_API_KEY        Claude via Pipecat's Anthropic service (optional: stub turn without it)
    PERSONA_VOICE_LLM_MODEL  default claude-haiku-4-5
    PERSONA_VOICE_STUB_LLM   1 = force the stub turn even with an Anthropic key
    PERSONA_VOICE_FAKE_VENDORS 1 = offline pipeline (tone TTS, no STT) for local transport proofs
    PERSONA_VOICE_MAX_CALL_SECS  hard cap per call, default 900
    PERSONA_TRACING          langfuse = export Pipecat OTel spans to Langfuse OTLP (agent/obs)

LLM slot (`llm_mode`): `flows` (Claude under Pipecat Flows over the shared brain) when
the call is live, ANTHROPIC_API_KEY is set and PERSONA_VOICE_STUB_LLM is off; otherwise
`stub` (templated echo, never moves session state).

The mode is decided once per process (`VoiceConfig.from_env()`), so the call route
can say up front whether it will return a real SDP answer.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping, Optional

DEFAULT_CARTESIA_VOICE = "71a7ad14-091c-4e8e-a314-022ece01c121"  # Cartesia stock voice
SAMPLE_RATE = 16000


def _flag(env: Mapping[str, str], name: str) -> bool:
    return (env.get(name) or "").strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class VoiceConfig:
    deepgram_key: Optional[str] = None
    cartesia_key: Optional[str] = None
    anthropic_key: Optional[str] = None
    stt_model: str = "nova-3"
    deepgram_tts_voice: str = "aura-2-thalia-en"
    cartesia_voice: str = DEFAULT_CARTESIA_VOICE
    cartesia_model: str = "sonic-2"
    llm_model: str = "claude-haiku-4-5"
    stub_llm: bool = False
    fake_vendors: bool = False
    max_call_secs: float = 900.0
    otel_langfuse: bool = False
    direct_speech: bool = True     # LAT-001: speak the brain's line via TTS (no phrasing LLM run)

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "VoiceConfig":
        env = os.environ if env is None else env
        return cls(
            deepgram_key=env.get("DEEPGRAM_API_KEY") or None,
            cartesia_key=env.get("CARTESIA_API_KEY") or None,
            anthropic_key=env.get("ANTHROPIC_API_KEY") or None,
            stt_model=env.get("DEEPGRAM_STT_MODEL") or cls.stt_model,
            deepgram_tts_voice=env.get("DEEPGRAM_TTS_VOICE") or cls.deepgram_tts_voice,
            cartesia_voice=env.get("CARTESIA_VOICE_ID") or cls.cartesia_voice,
            cartesia_model=env.get("CARTESIA_MODEL") or cls.cartesia_model,
            llm_model=env.get("PERSONA_VOICE_LLM_MODEL") or cls.llm_model,
            stub_llm=_flag(env, "PERSONA_VOICE_STUB_LLM"),
            fake_vendors=_flag(env, "PERSONA_VOICE_FAKE_VENDORS"),
            max_call_secs=float(env.get("PERSONA_VOICE_MAX_CALL_SECS") or cls.max_call_secs),
            otel_langfuse=(env.get("PERSONA_TRACING") or "").strip().lower() == "langfuse",
            direct_speech=(env.get("PERSONA_VOICE_DIRECT_SPEECH") or "1").strip().lower() not in ("0", "false", "no", "off"),
        )

    @property
    def mode(self) -> str:
        """`live` (vendor STT/TTS), `fake` (offline tone TTS), or `unavailable`."""
        if self.fake_vendors:
            return "fake"
        return "live" if self.deepgram_key else "unavailable"

    @property
    def can_answer(self) -> bool:
        return self.mode != "unavailable"

    @property
    def use_claude(self) -> bool:
        return self.mode == "live" and bool(self.anthropic_key) and not self.stub_llm

    @property
    def llm_mode(self) -> str:
        return "flows" if self.use_claude else "stub"

    def describe(self) -> dict:
        """Log-safe summary (never includes key values)."""
        return {
            "mode": self.mode,
            "stt": "deepgram" if self.mode == "live" else None,
            "tts": (["cartesia", "deepgram"] if self.cartesia_key else ["deepgram"]) if self.mode == "live" else ["tone"],
            "llm": "anthropic" if self.use_claude else "stub",
            "llm_mode": self.llm_mode,
        }
