"""GmailService: stored connection -> in-memory access token -> GmailClient.

- `access_token()` decrypts the stored refresh token and refreshes on use (cached in memory
  until near expiry). `invalid_grant`/revoked/undecryptable -> connection marked `invalid`
  and `ReconnectRequired` raised for the API to surface.
- `disconnect()` revokes at Google (best effort) and clears stored tokens; idempotent.
"""
from __future__ import annotations

import logging
import threading
from typing import Optional

import httpx

from ..store import PgStore
from .client import GmailClient
from .crypto import TokenCipher, TokenKeyError
from .demo import value_demo
from .oauth import AccessToken, GmailError, GoogleOAuth, ReconnectRequired

log = logging.getLogger(__name__)


class GmailService:
    def __init__(self, *, store: PgStore, cipher: Optional[TokenCipher], oauth: Optional[GoogleOAuth],
                 http: Optional[httpx.Client] = None):
        self.store, self.cipher, self.oauth = store, cipher, oauth
        self.http = http or (oauth.http if oauth else httpx.Client(timeout=10.0))
        self._lock = threading.Lock()
        self._access: dict[str, AccessToken] = {}  # memory only, never persisted

    # --- tokens --------------------------------------------------------------

    def seal(self, session_id: str, refresh_token: Optional[str]) -> dict:
        """Fields for the store's gmail upsert. Plaintext never leaves this method."""
        if not refresh_token:
            return {"refresh_token_enc": None, "token_status": "missing"}
        if self.cipher is None:
            log.warning("gmail: refresh token received but no token key configured; not stored")
            return {"refresh_token_enc": None, "token_status": "key_unavailable"}
        with self._lock:
            self._access.pop(session_id, None)
        return {"refresh_token_enc": self.cipher.encrypt(refresh_token, aad=session_id),
                "token_key_id": self.cipher.key_id, "token_status": "stored"}

    def access_token(self, session_id: str) -> str:
        with self._lock:
            cached = self._access.get(session_id)
        if cached and cached.fresh():
            return cached.token
        conn = self.store.gmail_connection(session_id)
        if conn is None:
            raise ReconnectRequired("not_connected")
        if conn.token_status != "stored" or conn.refresh_token_enc is None:
            raise ReconnectRequired(conn.token_status if conn.token_status != "stored" else "missing")
        if self.cipher is None or self.oauth is None:
            raise GmailError("gmail_unconfigured", status=503)
        try:
            refresh = self.cipher.decrypt(conn.refresh_token_enc, aad=session_id)
        except TokenKeyError:
            self.store.mark_gmail_invalid(session_id, error="undecryptable")
            raise ReconnectRequired("undecryptable") from None
        try:
            tok = self.oauth.refresh(refresh)
        except ReconnectRequired as e:
            self.store.mark_gmail_invalid(session_id, error=e.reason)
            raise
        self.store.mark_gmail_refreshed(session_id)
        with self._lock:
            self._access[session_id] = tok
        return tok.token

    def client(self, session_id: str) -> GmailClient:
        return GmailClient(self.access_token(session_id), http=self.http)

    def demo(self, session_id: str, *, limit: int = 3) -> list[dict]:
        try:
            return value_demo(self.client(session_id), limit=limit)
        except ReconnectRequired:
            with self._lock:
                self._access.pop(session_id, None)
            raise

    # --- disconnect ------------------------------------------------------------

    def disconnect(self, session_id: str) -> bool:
        """Revoke at Google (best effort) + clear stored tokens + set revoked_at. Idempotent:
        returns True only when this call revoked the stored connection."""
        with self._lock:
            cached = self._access.pop(session_id, None)
        conn = self.store.gmail_connection(session_id)
        if conn is None:
            return False
        if conn.revoked_at is None and self.oauth is not None:
            token = cached.token if cached else None
            if conn.refresh_token_enc is not None and self.cipher is not None:
                try:
                    token = self.cipher.decrypt(conn.refresh_token_enc, aad=session_id)  # revokes the whole grant
                except TokenKeyError:
                    pass
            if token:
                try:
                    self.oauth.revoke(token)
                except GmailError as e:
                    log.warning("gmail: google revoke failed (%s); clearing stored tokens anyway", e.code)
        return self.store.revoke_gmail(session_id)
