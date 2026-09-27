"""Confirm gate for Gmail write actions (draft/send/modify).

Nothing is sent or changed without the user's explicit OK (docs/product-facts.md). Flow:
  1. a turn proposes an action -> `gate.propose(session_id, action, params)` returns a
     single-use `confirm_token` bound to (session, action, exact params);
  2. a *later* turn in which the user says yes -> `gate.confirm(session_id, token, action, params)`
     returns a `Confirmed` grant;
  3. `GmailClient` write methods refuse without a matching, unused grant.
Changing the draft (recipient, body, labels) changes the digest, so the old OK doesn't carry over.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any

WRITE_ACTIONS = frozenset({"draft", "send", "modify"})
_MINT = object()  # only ConfirmGate can construct a valid Confirmed


class ConfirmationRequired(PermissionError):
    def __init__(self, action: str, reason: str):
        super().__init__(f"{action}: explicit user confirmation required ({reason})")
        self.action, self.reason = action, reason


def action_digest(action: str, params: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps([action, params], sort_keys=True, default=str).encode()).hexdigest()


@dataclass
class Confirmed:
    session_id: str
    action: str
    digest: str
    _mint: object = field(default=None, repr=False)
    used: bool = False

    def __post_init__(self) -> None:
        if self._mint is not _MINT:
            raise ConfirmationRequired(self.action, "grant_not_issued_by_gate")


@dataclass
class _Pending:
    session_id: str
    action: str
    digest: str
    expires_at: float


class ConfirmGate:
    def __init__(self, *, ttl_s: float = 600.0):
        self.ttl_s = ttl_s
        self._lock = threading.Lock()
        self._pending: dict[str, _Pending] = {}

    def propose(self, session_id: str, action: str, params: dict[str, Any]) -> str:
        if action not in WRITE_ACTIONS:
            raise ValueError(f"unknown write action {action!r}")
        token = secrets.token_urlsafe(24)
        with self._lock:
            self._pending[token] = _Pending(session_id, action, action_digest(action, params),
                                            time.monotonic() + self.ttl_s)
        return token

    def confirm(self, session_id: str, confirm_token: str | None, action: str, params: dict[str, Any]) -> Confirmed:
        if not confirm_token:
            raise ConfirmationRequired(action, "missing_confirm_token")
        with self._lock:
            p = self._pending.pop(confirm_token, None)  # single use, even on mismatch
        if p is None:
            raise ConfirmationRequired(action, "unknown_or_used_confirm_token")
        if time.monotonic() > p.expires_at:
            raise ConfirmationRequired(action, "confirm_token_expired")
        if p.session_id != session_id or p.action != action or p.digest != action_digest(action, params):
            raise ConfirmationRequired(action, "confirm_token_mismatch")
        return Confirmed(session_id, action, p.digest, _mint=_MINT)


def require(confirmed: Confirmed | None, action: str, params: dict[str, Any]) -> None:
    """Called by every write path immediately before any HTTP request."""
    if not isinstance(confirmed, Confirmed) or confirmed._mint is not _MINT:
        raise ConfirmationRequired(action, "missing_confirmation")
    if confirmed.used:
        raise ConfirmationRequired(action, "confirmation_already_used")
    if confirmed.action != action or confirmed.digest != action_digest(action, params):
        raise ConfirmationRequired(action, "confirmation_mismatch")
    confirmed.used = True
