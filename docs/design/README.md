# docs/design — owned by DESIGN workers

Process: research (references/, research.md) → spec (spec.md, tokens.json) → mockups
(mockups/<state>@desktop.png, <state>@mobile.png + HTML/CSS source) → freeze (the FE packet
cites the commit SHA) → FE implements → EM design-review gate compares screenshots of the
running app with these mockups (`npm run qa:visual`).

State names (shared with harness/visual and FE): landing, chat-agent-name, call-offer,
call-ringing, call-connected, call-reconnecting, gmail-card-idle, gmail-card-connected,
gmail-card-error, graduation, welcome-back.

Skills: `.claude/skills/frontend-design` (mandatory), `.claude/skills/webapp-testing`.
Reference screenshots of third-party products are for analysis only and never ship.
