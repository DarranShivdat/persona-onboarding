"""Minimal Gmail REST client (users.me). Reads are free; writes are confirm-gated."""
from __future__ import annotations

import base64
from email.message import EmailMessage
from typing import Any, Optional

import httpx

from .confirm import Confirmed, require
from .oauth import GmailError, ReconnectRequired

API = "https://gmail.googleapis.com/gmail/v1/users/me"
METADATA_HEADERS = ("From", "Subject", "Date")


class GmailClient:
    def __init__(self, access_token: str, *, http: httpx.Client):
        self._token, self.http = access_token, http

    def __repr__(self) -> str:
        return "GmailClient()"

    # --- reads ---------------------------------------------------------------

    def list_messages(self, *, max_results: int = 3, label_ids: tuple[str, ...] = ("INBOX",),
                      query: Optional[str] = None) -> list[dict]:
        params: list[tuple[str, Any]] = [("maxResults", max_results), *(("labelIds", l) for l in label_ids)]
        if query:
            params.append(("q", query))
        return self._req("GET", "/messages", params=params).get("messages") or []

    def get_metadata(self, message_id: str) -> dict:
        params = [("format", "metadata"), *(("metadataHeaders", h) for h in METADATA_HEADERS)]
        return self._req("GET", f"/messages/{message_id}", params=params)

    # --- writes (explicit confirm required) ----------------------------------

    def create_draft(self, *, to: str, subject: str, body: str, confirmed: Optional[Confirmed]) -> dict:
        params = {"to": to, "subject": subject, "body": body}
        require(confirmed, "draft", params)
        return self._req("POST", "/drafts", json={"message": {"raw": _raw(**params)}})

    def send(self, *, to: str, subject: str, body: str, confirmed: Optional[Confirmed]) -> dict:
        params = {"to": to, "subject": subject, "body": body}
        require(confirmed, "send", params)
        return self._req("POST", "/messages/send", json={"raw": _raw(**params)})

    def modify(self, message_id: str, *, add_labels: tuple[str, ...] = (), remove_labels: tuple[str, ...] = (),
               confirmed: Optional[Confirmed]) -> dict:
        params = {"id": message_id, "add": sorted(add_labels), "remove": sorted(remove_labels)}
        require(confirmed, "modify", params)
        return self._req("POST", f"/messages/{message_id}/modify",
                         json={"addLabelIds": params["add"], "removeLabelIds": params["remove"]})

    # --- transport -------------------------------------------------------------

    def _req(self, method: str, path: str, **kw) -> dict:
        try:
            r = self.http.request(method, API + path, headers={"Authorization": f"Bearer {self._token}"}, **kw)
        except httpx.HTTPError as e:
            raise GmailError("google_unavailable", type(e).__name__) from None
        if r.status_code == 401:
            raise ReconnectRequired("access_token_rejected")
        if r.status_code == 403:
            raise GmailError("insufficient_scope", status=403)  # partial grant: reduced capability
        if r.status_code >= 400:
            raise GmailError("gmail_api_error", f"http_{r.status_code}", status=r.status_code)
        return r.json() if r.content else {}


def _raw(*, to: str, subject: str, body: str) -> str:
    m = EmailMessage()
    m["To"], m["Subject"] = to, subject
    m.set_content(body)
    return base64.urlsafe_b64encode(m.as_bytes()).decode()
