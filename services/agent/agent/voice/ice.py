"""ICE server config shared by both peers (browser + aiortc). From the INFRA-001 spike.

The same list goes to the browser and to the server's SmallWebRTCConnection: on hosts
without inbound UDP (Fly) the server leg must relay through TURN too. See
docs/decisions/0001-voice-hosting.md and infra/voice-spike/README.md for env names.
"""
from __future__ import annotations

import os

import httpx
from loguru import logger

DEFAULT_STUN = "stun:stun.l.google.com:19302"
CLOUDFLARE_TURN_URL = (
    "https://rtc.live.cloudflare.com/v1/turn/keys/{key_id}/credentials/generate-ice-servers"
)


def _split(value: str | None) -> list[str]:
    return [v.strip() for v in (value or "").split(",") if v.strip()]


def static_ice_servers(env: dict[str, str] | None = None) -> list[dict]:
    """ICE servers from static env config, in RTCPeerConnection JSON shape."""
    env = os.environ if env is None else env
    servers: list[dict] = []
    stun = _split(env.get("PERSONA_STUN_URLS")) or [DEFAULT_STUN]
    if stun != ["none"]:  # "none" = host candidates only (offline local smoke)
        servers.append({"urls": stun})
    turn = _split(env.get("PERSONA_TURN_URLS"))
    if turn:
        servers.append(
            {
                "urls": turn,
                "username": env.get("PERSONA_TURN_USERNAME", ""),
                "credential": env.get("PERSONA_TURN_CREDENTIAL", ""),
            }
        )
    return servers


async def cloudflare_ice_servers(ttl_secs: int = 3600) -> list[dict] | None:
    """Mint short-lived Cloudflare Realtime TURN credentials, if configured."""
    key_id = os.environ.get("CLOUDFLARE_TURN_KEY_ID")
    token = os.environ.get("CLOUDFLARE_TURN_API_TOKEN")
    if not (key_id and token):
        return None
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.post(
            CLOUDFLARE_TURN_URL.format(key_id=key_id),
            headers={"Authorization": f"Bearer {token}"},
            json={"ttl": ttl_secs},
        )
        resp.raise_for_status()
        servers = resp.json()["iceServers"]
    # Cloudflare also returns :53 URLs; browsers block port 53 and stall gathering on
    # them, and Cloudflare's docs recommend filtering them out.
    for s in servers:
        urls = s["urls"] if isinstance(s["urls"], list) else [s["urls"]]
        s["urls"] = [u for u in urls if ":53?" not in u and not u.endswith(":53")]
    return [s for s in servers if s["urls"]]


ICE_TTL_S = 3600


async def resolve_ice() -> tuple[list[dict], int | None]:
    """(servers, ttl_s): Cloudflare-minted creds (ttl) if configured, else static (no ttl)."""
    try:
        minted = await cloudflare_ice_servers(ICE_TTL_S)
    except Exception as e:  # noqa: BLE001 - fall back to static config (never log the token)
        logger.warning(f"Cloudflare TURN mint failed, using static ICE config: {type(e).__name__}")
        minted = None
    return (minted, ICE_TTL_S) if minted else (static_ice_servers(), None)


async def resolve_ice_servers() -> list[dict]:
    return (await resolve_ice())[0]


def ice_mode(env: dict[str, str] | None = None) -> str:
    """Log-safe summary of which ICE source is configured: cloudflare | static_turn | stun_only."""
    env = os.environ if env is None else env
    if env.get("CLOUDFLARE_TURN_KEY_ID") and env.get("CLOUDFLARE_TURN_API_TOKEN"):
        return "cloudflare"
    return "static_turn" if _split(env.get("PERSONA_TURN_URLS")) else "stun_only"


def to_aiortc(servers: list[dict]) -> list:
    """Browser-shaped ICE JSON -> aiortc RTCIceServer.

    aioice rejects URL schemes it does not know, so keep only stun:/turn:/turns:.
    """
    from pipecat.transports.smallwebrtc.connection import IceServer

    out: list[IceServer] = []
    for s in servers:
        urls = s["urls"] if isinstance(s["urls"], list) else [s["urls"]]
        urls = [u for u in urls if u.split(":", 1)[0] in ("stun", "turn", "turns")]
        if not urls:
            continue
        out.append(
            IceServer(urls=urls, username=s.get("username"), credential=s.get("credential"))
        )
    return out
