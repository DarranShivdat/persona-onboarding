# agent/api — HTTP surface used by apps/web (packet FLOW-003)

- `POST /v1/sessions` -> create session, returns id (web stores it in a cookie)
- `GET  /v1/sessions/{id}` -> state snapshot (slots, node, deferred, call status)
- `POST /v1/sessions/{id}/turns` -> text turn: extraction (Anthropic SDK) -> brain -> phrasing
- `POST /v1/sessions/{id}/gmail` -> server-to-server from the Next.js OAuth callback
  with the verified address (shared secret); fills `gmail`, notifies a live call
- `POST /v1/sessions/{id}/call` -> SmallWebRTC offer/answer (acquires the call lease)
- `GET  /v1/sessions/{id}/events` -> SSE stream (UI pushes: gmail card, transcript, state)
- `GET  /health`

Rate limits per IP + per session; all mutating routes require the session token.
