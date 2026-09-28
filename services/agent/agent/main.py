"""Agent process entrypoint: `python -m agent.main --host 0.0.0.0 --port 8080`.

Serves `agent.api.app:create_app_from_env()` (text turns, SSE, call lease + hosted voice
pipeline) under uvicorn. At boot it prints an env report of MISSING variable NAMES and the
features they disable (never values), and starts a best-effort, time-boxed warmup of the
voice models (Silero VAD + Smart Turn) in a background thread so `/health` answers at once.

Warmup pattern adapted from Penciled voice-agent `warmup.py` (Darran's IP).
"""
from __future__ import annotations

import argparse
import os
import sys
import threading
import time
from typing import Mapping, Optional

from loguru import logger

# (feature, required names (all), alternatives (any one group suffices), what's off without it)
FEATURES: list[tuple[str, list[str], list[list[str]], str]] = [
    ("database", ["PERSONA_DATABASE_URL"], [], "agent cannot start"),
    ("gmail_route", ["PERSONA_INTERNAL_SECRET"], [], "POST/DELETE /gmail answer 503 (OAuth callback can't fill gmail)"),
    ("gmail_tokens", ["PERSONA_TOKEN_ENCRYPT_KEY"], [], "refresh tokens not stored (token_status=key_unavailable)"),
    ("gmail_oauth", ["GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET"], [], "gmail demo/revoke can't refresh tokens"),
    ("voice", [], [["DEEPGRAM_API_KEY"], ["PERSONA_VOICE_FAKE_VENDORS"]],
     "POST /call is lease-only (answer: null); the browser falls back to text"),
    ("voice_claude", ["ANTHROPIC_API_KEY"], [], "calls use the stub turn (no Claude on voice)"),
    ("text_claude", ["ANTHROPIC_API_KEY"], [], "text chat uses FakeLlm (naive extraction + fixed templates)"),
    ("voice_cartesia", ["CARTESIA_API_KEY"], [], "Deepgram TTS is primary (no TTS failover)"),
    ("turn", [], [["CLOUDFLARE_TURN_KEY_ID", "CLOUDFLARE_TURN_API_TOKEN"],
                  ["PERSONA_TURN_URLS", "PERSONA_TURN_USERNAME", "PERSONA_TURN_CREDENTIAL"]],
     "STUN only: UDP-blocked callers / hosts without inbound UDP can't connect"),
    ("tracing", ["LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"], [], "traces are not exported (noop tracer)"),
]


def env_report(env: Optional[Mapping[str, str]] = None) -> dict:
    """{"missing": {feature: [NAMES]}, "disabled": {feature: why}, "enabled": [features]}.
    Only variable names and feature names: safe to log."""
    env = os.environ if env is None else env
    has = lambda n: bool((env.get(n) or "").strip())  # noqa: E731
    missing: dict[str, list[str]] = {}
    disabled: dict[str, str] = {}
    enabled: list[str] = []
    for feature, required, alternatives, why in FEATURES:
        if feature == "tracing" and (env.get("PERSONA_TRACING") or "noop").strip().lower() != "langfuse":
            disabled[feature] = "PERSONA_TRACING is not 'langfuse' (noop tracer)"
            continue
        names = [n for n in required if not has(n)]
        if alternatives and not any(all(has(n) for n in group) for group in alternatives):
            names.append(" | ".join(" + ".join(g) for g in alternatives))
        if names:
            missing[feature], disabled[feature] = names, why
        else:
            enabled.append(feature)
    return {"missing": missing, "disabled": disabled, "enabled": enabled}


def log_env_report(env: Optional[Mapping[str, str]] = None) -> dict:
    report = env_report(env)
    from .voice.config import VoiceConfig
    from .voice.ice import ice_mode

    voice = VoiceConfig.from_env(env)
    logger.info(f"env: enabled={report['enabled']} voice={voice.describe()} ice={ice_mode(env)}")
    for feature, names in report["missing"].items():
        logger.warning(f"env: {feature} disabled — missing {', '.join(names)} ({report['disabled'][feature]})")
    for feature, why in report["disabled"].items():
        if feature not in report["missing"]:
            logger.info(f"env: {feature} off — {why}")
    return report


class Warmup:
    """Load the voice models once so the first call doesn't pay for them. Best effort:
    failures and timeouts are logged, never fatal."""

    def __init__(self, timeout_s: float = 20.0):
        self.timeout_s = timeout_s
        self.state = "pending"
        self.took_s: Optional[float] = None

    def run(self) -> None:
        from .voice.config import VoiceConfig

        cfg = VoiceConfig.from_env()
        if not cfg.can_answer:
            self.state = "skipped"
            return
        t0 = time.monotonic()
        self.state = "running"
        done = threading.Event()

        def work() -> None:
            try:
                from .voice.session import build_user_params

                build_user_params(cfg)  # Silero VAD + Smart Turn v3 (downloads/loads ONNX)
                self.state = "ok"
            except Exception as e:  # noqa: BLE001
                self.state = "failed"
                logger.warning(f"warmup failed (calls still work, first one is slower): {type(e).__name__}: {e}")
            finally:
                done.set()

        threading.Thread(target=work, name="warmup", daemon=True).start()
        if not done.wait(self.timeout_s):
            self.state = "timeout"
            logger.warning(f"warmup still running after {self.timeout_s:.0f}s; serving anyway")
            return
        self.took_s = round(time.monotonic() - t0, 2)
        logger.info(f"warmup {self.state} in {self.took_s}s")

    def start(self) -> threading.Thread:
        t = threading.Thread(target=self.run, name="warmup-watch", daemon=True)
        t.start()
        return t


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="python -m agent.main",
                                description="Persona onboarding agent (text API + hosted voice calls).")
    p.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    p.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8080")))
    p.add_argument("--log-level", default=os.environ.get("LOG_LEVEL", "info"))
    p.add_argument("--no-warmup", action="store_true", help="skip the voice model warmup")
    p.add_argument("--warmup-timeout", type=float, default=float(os.environ.get("PERSONA_WARMUP_TIMEOUT_S", "20")))
    p.add_argument("--env-report", action="store_true", help="print the env report (names only) and exit")
    return p.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    report = log_env_report()
    if args.env_report:
        return 0 if "database" not in report["missing"] else 1
    if "database" in report["missing"]:
        logger.error("PERSONA_DATABASE_URL is required")
        return 1
    import uvicorn

    from .api.app import create_app_from_env

    if not args.no_warmup:
        Warmup(args.warmup_timeout).start()
    # Graceful shutdown hangs up live calls (app lifespan); give it a moment.
    uvicorn.run(create_app_from_env(), host=args.host, port=args.port, log_level=args.log_level,
                timeout_graceful_shutdown=10)
    return 0


if __name__ == "__main__":
    sys.exit(main())
