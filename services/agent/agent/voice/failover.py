"""TTS failover: Cartesia primary -> Deepgram TTS, via Pipecat's ServiceSwitcher.

Pipecat's stock `ServiceSwitcherStrategyFailover` only switches when the failing
service has become unusable, and services only lose usability on *permanent* error
categories (auth / invalid request). Most real Cartesia failures mid-call (socket
drop, 5xx, "context completed with no audio", timeouts) are reported as UNKNOWN /
CONNECTIVITY / SERVER, so the stock strategy would keep routing speech into a dead
provider — dead air on a call (invariant 7). Older Pipecat (1.4) instead rotates
round-robin, flapping back onto a dead provider. `TtsFailoverStrategy` fails over on
any provider-side error from the active TTS: it marks the service failed for the rest
of the call and moves to the next healthy one. Errors raised by application code
(`ErrorCategory.APPLICATION`) are not the provider's fault and never switch.

Works on Pipecat versions with and without `pipecat.utils.errors` / service usability:
error categories are read with `getattr` and failed services are tracked here.

No flapping back: once a call is on the fallback voice it stays there (a voice change
mid-sentence is worse than a slightly different voice). New calls build fresh services.

Pattern adapted from Penciled voice-agent `services.py::build_tts` (Darran's IP).
"""
from __future__ import annotations

from typing import Awaitable, Callable, Optional, Sequence

from loguru import logger
from pipecat.frames.frames import ErrorFrame
from pipecat.pipeline.service_switcher import ServiceSwitcher, ServiceSwitcherStrategyFailover
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

try:  # Pipecat >= the release that added error categories
    from pipecat.utils.errors import ErrorCategory
except ImportError:  # Pipecat 1.4: no categories on ErrorFrame
    from enum import Enum

    class ErrorCategory(str, Enum):
        UNKNOWN = "unknown"
        CONNECTIVITY = "connectivity"
        SERVER = "server"
        RATE_LIMIT = "rate_limit"
        AUTHENTICATION = "authentication"
        INVALID_REQUEST = "invalid_request"
        APPLICATION = "application"

OnSwitched = Callable[[FrameProcessor], Awaitable[None]]


def error_category(error: ErrorFrame) -> Optional[ErrorCategory]:
    return getattr(error, "category", None)


class TtsFailoverStrategy(ServiceSwitcherStrategyFailover):
    def __init__(self, services):  # noqa: ANN001
        super().__init__(services)
        self._failed: set[int] = set()

    def is_healthy(self, service: FrameProcessor) -> bool:
        return id(service) not in self._failed and getattr(service, "is_usable", True)

    @property
    def exhausted(self) -> bool:
        return not any(self.is_healthy(s) for s in self._services)

    async def handle_error(self, error: ErrorFrame) -> Optional[FrameProcessor]:
        failed = error.processor or self._active_service
        if failed is not self._active_service:
            return None
        if error_category(error) == ErrorCategory.APPLICATION:
            return None
        if self.is_healthy(failed):
            logger.warning(f"[TTS FAILOVER] {failed.name} failed ({error.error}); marking unusable for this call")
            self._failed.add(id(failed))
            if hasattr(failed, "set_usable"):
                await failed.set_usable(False)
        # Next healthy service after the failed one, in list order; never flap back.
        idx = self._services.index(failed)
        for svc in self._services[idx + 1:] + self._services[:idx]:
            if self.is_healthy(svc):
                return await self._set_active_if_available(svc)
        logger.error("[TTS FAILOVER] no healthy TTS left")
        return None


class TtsFailoverSwitcher(ServiceSwitcher):
    """ServiceSwitcher that absorbs errors a successful failover already handled.

    Only an exhausted switcher lets the provider error continue upstream, so app-level
    error handlers do not tear down a call the backup voice is still serving.
    """

    @property
    def is_usable(self) -> bool:
        return not self.strategy.exhausted

    async def push_frame(self, frame, direction: FrameDirection = FrameDirection.DOWNSTREAM):  # noqa: ANN001
        if (
            isinstance(frame, ErrorFrame)
            and not frame.fatal
            and frame.processor is not None
            and frame.processor is self.strategy.active_service
        ):
            await self.strategy.handle_error(frame)
            if error_category(frame) != ErrorCategory.APPLICATION and not self.strategy.exhausted:
                return  # failed over: the backup voice keeps the call alive
            # Skip ServiceSwitcher.push_frame so the strategy doesn't see this error twice.
            await super(ServiceSwitcher, self).push_frame(frame, direction)
            return
        await super().push_frame(frame, direction)


def build_tts_switcher(services: Sequence[FrameProcessor], on_switched: Optional[OnSwitched] = None) -> FrameProcessor:
    """One service -> itself; several -> a ServiceSwitcher failing over in list order."""
    services = list(services)
    if len(services) == 1:
        return services[0]
    switcher = TtsFailoverSwitcher(services=services, strategy_type=TtsFailoverStrategy)

    @switcher.strategy.event_handler("on_service_switched")
    async def _on_switched(_strategy, service):  # noqa: ANN001
        logger.warning(f"[TTS FAILOVER] now using {service.name}")
        if on_switched:
            await on_switched(service)

    return switcher
