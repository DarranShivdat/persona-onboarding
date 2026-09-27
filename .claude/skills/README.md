# Vendored Claude Code skills

Project-scoped skills (Claude Code loads `.claude/skills/*/SKILL.md` automatically).
Copied verbatim from the public **anthropics/skills** repository, Apache-2.0
(each skill keeps its `LICENSE.txt`). Source commit: `3337550` (2026-09-24).
Verified to exist at:
- https://github.com/anthropics/skills/tree/main/skills/frontend-design
  (identical copy also ships in anthropics/claude-code `plugins/frontend-design`)
- https://github.com/anthropics/skills/tree/main/skills/webapp-testing
- https://github.com/anthropics/skills/tree/main/skills/claude-api

| Skill | Used by | Why |
|---|---|---|
| `frontend-design` | DESIGN, FRONTEND workers | aesthetic direction, typography, non-templated UI |
| `webapp-testing` | FRONTEND worker, EM visual gate | Playwright-driven verification + screenshots |
| `claude-api` | impl workers on LLM code | current model ids/params (e.g. Opus 5.5 breaking changes: no forced tool_choice, thinking always on) |

Update: re-copy from upstream and bump the commit above in the same commit.
