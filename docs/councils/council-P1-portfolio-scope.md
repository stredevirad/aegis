# Council P1: Portfolio scope

**Date:** 2026-11-08 · **Protocol:** `llm-council` skill (5 advisors → 5 anonymized peer reviews → chairman synthesis), run by the owner outside this repo. This is a summary; the full transcript isn't stored here.

## Question
How much of the original production roadmap (M0–M11) is still worth building, given that the goal is a portfolio project a student owner can defend in interviews, on a Pro-plan budget and a 16 GB laptop?

## Verdict
- **Progress:** about 55% of the full production roadmap and about 70–75% of a portfolio definition of done. The engineering depth (tenancy, queue, egress, audit, bounded LLM, remediation) already exceeds portfolio needs.
- **Gap:** visibility, not engineering. Nothing is on GitHub, there's no one-command demo, no CI, a stale README and no screenshots.
- **In scope (Phases 1–5):** repo hygiene and first push; UI against the real backend with seed data and a committed Playwright E2E; backend Dockerfile and one-command compose stack; GitHub Actions CI; README, GIF, truthful docs and a release.
- **Cut, recorded as DEFERRED with reasons:** Helm/Kubernetes (M9); SBOM, image scanning and supply-chain hardening beyond gitleaks and Dependabot; load and chaos campaigns (M10); 25 scenario types; Council E / M11 war-room; Tempo and Kubernetes collectors; Slack/PagerDuty/webhook notifications; LLM cost accounting; live OpenAI/Anthropic runs; retention sweeps; topology graph; production MCP client hardening; hosted demo site.
- **Remediation UI** is out of the core path. The two-person backend lifecycle stays and is documented. A minimal panel is stretch S3.
- **Stretch, only on the owner's "go S<n>":** S1 LLM-adjudication evaluation, S2 RCAEval public benchmark, S3 remediation panel, S4 read-only MCP server.

## Constraints carried into the plan
Never rewrite history (docs cite commit hashes). Keep every honesty caveat on the synthetic benchmark and the single live run. Every README number gets a reproduce command. Work in small, pushed checkpoints so a usage cut-off loses nothing.

Supersedes the open deployment questions from [Council D](council-D-note.md). Progress: [`docs/PORTFOLIO_PROGRESS.md`](../PORTFOLIO_PROGRESS.md).
