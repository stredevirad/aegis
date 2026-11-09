# Council P2: Agent tooling for the portfolio release

**Date:** 2026-11-08 · **Protocol:** `llm-council` skill (5 advisors → 5 anonymized peer reviews → chairman synthesis), run by the owner outside this repo. This is a summary; the full transcript isn't stored here.

## Question
Which tools should the coding agent (Claude Code) use to finish Aegis on a Pro plan and a 16 GB Windows laptop: MCP servers, CLIs or browser automation, and with which permissions?

## Verdict
- **CLIs first:** `gh`, `git`, `docker`, `uv`, `npm`, `curl`, `ffmpeg`. They're cheaper in context than MCP servers, and the same commands work in CI and in the README.
- **Browser automation is the committed `@playwright/test` suite.** One rerunnable script is the E2E test, the CI job and the generator for the README GIF and screenshots. Ad-hoc browser sessions leave no artifact.
- **Library docs:** check local type definitions (`node_modules/**/*.d.ts`), installed package source and `tsc`/`mypy` output first. Use Context7 for config/migration docs of new majors (ESLint 10 flat config, Vite 8, TS 6, React Router 7.18, Vitest 5, FastAPI 0.142). Never guess an API from memory twice.
- **Playwright MCP is a fallback only** (local scope, added by the owner) for a UI bug the script can't reproduce. Remove it afterwards.
- **Not used:** GitHub MCP, Serena, Postgres/Docker/observability MCPs, Chrome DevTools MCP, Sequential Thinking, Memory and Filesystem MCPs.
- **Models:** Sonnet for mechanical phases (hygiene, CI, docs). Plan-mode Opus ("opusplan") for the UI and Docker phases. Opus xhigh only for evaluations or a bug Sonnet failed twice.
- **Permissions:** the owner runs auto mode with deny rules in `.claude/settings.json`. Deny rules are a seatbelt, not a boundary: the plan's rules on history, visibility, deletion and secrets bind the agent regardless.
- **Subagents:** only where a phase calls for one (the Phase 5 stranger review), on Sonnet with a narrow task.

## Note (2026-11-09)
At the start of Phase 1, `.claude/settings.json` did not exist, so no deny rules were active. The owner should add them if the seatbelt is wanted.
