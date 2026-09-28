# Product facts (approved answers the agent may state)

The phrasing layer and the LLM judge treat this file as the ONLY source of product,
privacy, and capability claims. Anything not here must not be asserted.
Status: DRAFT — Darran to approve (D-class). Updated 2026-09-26 with Darran's decisions.

## About Persona (public info from yourpersona.com; keep claims this general)
- Persona is a personal AI assistant that gets things done: you text it, and it helps
  with your email, calendar, and everyday tasks.
- Persona asks for your confirmation before it acts on your behalf.
- Persona Band is Persona's screenless wearable for talking to your assistant by voice.
- Do NOT state prices, ship dates, funding, company certifications (e.g. SOC 2), or
  encryption claims about Persona's production service — this trial build is separate.

## Gmail connection (this trial build)
- Gmail connects through Google sign-in (OAuth); the assistant never sees your password.
- Persona asks for **read and write** access to Gmail, because most of what it does for
  you is automation:
  - read your email (to understand your inbox and what needs attention),
  - organize it (labels, archive, mark as read, drafts),
  - send email for you — only after you say OK.
- Nothing is sent or changed in your inbox without your explicit OK.
- It also confirms which Google account you connected (your email address and name).
- During this trial the Google app is in testing mode: only invited test accounts can
  connect, and Google shows an "unverified app" notice you can continue past.
- In testing mode Google asks you to reconnect about once a week.
- Your Google tokens are stored encrypted; you can disconnect Gmail at any time
  (Settings → Disconnect), which revokes access and deletes the stored tokens.

## Voice
- Voice calls are processed by speech providers (speech-to-text and
  text-to-speech) to run the conversation. Call audio is not saved.

## Retention and contact (approved by Darran, 2026-09-27)
- Setup conversations and Gmail connection records are kept no longer than 30 days after
  your last activity; disconnecting Gmail deletes the stored Google tokens immediately.
- Questions or deletion requests: darranshivdat1@gmail.com (also on /privacy).
