"""Google OAuth token endpoint: refresh + revoke. HTTP goes through an injected
`httpx.Client` so tests replay recorded fixtures via `httpx.MockTransport`."""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Optional

import httpx

TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
# Google errors that mean "this grant is dead; the user must reconnect".
RECONNECT_ERRORS = {"invalid_grant", "unauthorized_client", "invalid_client"}


class GmailError(RuntimeError):
    """Structured Gmail/Google failure. `code` is safe to surface; never contains token data."""

    def __init__(self, code: str, detail: str = "", *, status: Optional[int] = None):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code, self.detail, self.status = code, detail, status


class ReconnectRequired(GmailError):
    """The stored grant is unusable (invalid_grant, revoked, missing, undecryptable) —
    the API surfaces this as 409 `gmail_reconnect_required` so the UI shows Reconnect."""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__("gmail_reconnect_required", detail, status=409)
        self.reason = reason


@dataclass(frozen=True)
class AccessToken:
    token: str = field(repr=False)
    expires_at: float
    scopes: tuple[str, ...] = ()

    def fresh(self, skew_s: float = 60.0) -> bool:
        return time.time() < self.expires_at - skew_s


class GoogleOAuth:
    def __init__(self, client_id: str, client_secret: str, *, http: httpx.Client):
        self.client_id, self._secret, self.http = client_id, client_secret, http

    def __repr__(self) -> str:
        return f"GoogleOAuth(client_id={self.client_id!r})"

    @classmethod
    def from_env(cls, *, http: Optional[httpx.Client] = None) -> Optional["GoogleOAuth"]:
        cid, sec = os.environ.get("GOOGLE_OAUTH_CLIENT_ID"), os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET")
        if not (cid and sec):
            return None
        return cls(cid, sec, http=http or httpx.Client(timeout=10.0))

    def refresh(self, refresh_token: str) -> AccessToken:
        try:
            r = self.http.post(TOKEN_URL, data={
                "grant_type": "refresh_token", "refresh_token": refresh_token,
                "client_id": self.client_id, "client_secret": self._secret})
        except httpx.HTTPError as e:
            raise GmailError("google_unavailable", type(e).__name__) from None
        body = _json(r)
        if r.status_code == 200 and body.get("access_token"):
            return AccessToken(body["access_token"], time.time() + float(body.get("expires_in", 3600)),
                               tuple((body.get("scope") or "").split()))
        err = str(body.get("error") or f"http_{r.status_code}")
        if err in RECONNECT_ERRORS:
            raise ReconnectRequired(err, str(body.get("error_description") or ""))
        raise GmailError("token_refresh_failed", err, status=r.status_code)

    def revoke(self, token: str) -> bool:
        """True if Google revoked it or already considered it invalid (both mean: gone)."""
        try:
            r = self.http.post(REVOKE_URL, data={"token": token})
        except httpx.HTTPError as e:
            raise GmailError("google_unavailable", type(e).__name__) from None
        return r.status_code == 200 or (r.status_code == 400 and _json(r).get("error") == "invalid_token")


def _json(r: httpx.Response) -> dict:
    try:
        body = r.json()
        return body if isinstance(body, dict) else {}
    except ValueError:
        return {}
