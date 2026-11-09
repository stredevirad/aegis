# Council P2: Agent tooling for the portfolio release

**Date:** 2026-11-08 · **Protocol:** `llm-council` skill (5 advisors → 5 anonymized peer reviews → chairman synthesis). This is a summary; the full transcript isn't stored in this repo.

## Question
Which tools should the coding agent (Claude Code) use to finish Aegis on a Pro plan and a 16 GB Windows laptop: MCP servers, CLIs or browser automation, and with which permissions?

## Verdict
- **CLIs first:** `gh`, `git`, `docker`, `uv`, `npm`, `curl`, `ffmpeg`.
- **Browser automation is the committed `@playwright/test` suite.** It's rerunnable, and it doubles as the E2E test and the generator for the README GIF and screenshots.
- **Library docs:** check local type definitions (`node_modules/**/*.d.ts`), installed package source and `tsc`/`mypy` output first. Use Context7 for config/migration docs of new majors (ESLint 10 flat config, Vite 8, TS 6, React Router 7.18, Vitest 5, FastAPI 0.142). Never guess an API from memory twice.
- **Playwright MCP is a fallback only** (local scope, added by the owner) for a UI bug the script can't reproduce. Remove it afterwards.
- **Not used:** GitHub MCP, Serena, Postgres/Docker/observability MCPs, Chrome DevTools MCP, Sequential Thinking, Memory and Filesystem MCPs.
- **Permissions:** the owner runs auto mode with deny rules in `.claude/settings.json`. Deny rules are a seatbelt, not a boundary: the plan's rules on history, visibility, deletion and secrets bind the agent regardless.

## Related rules from the brief (not council findings)
Per-phase model/effort recommendations come from the brief's session protocol. Subagent limits come from its operating rule 10.

## Note (2026-11-09)
At the start of Phase 1, `.claude/settings.json` did not exist, so no deny rules were active. The owner should add them if the seatbelt is wanted.
