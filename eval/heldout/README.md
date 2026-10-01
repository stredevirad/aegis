# Held-out evaluation set (frozen 2026-10-01)

Eight real OpenTelemetry Demo captures from the research phase (one healthy baseline, five
`productCatalogFailure` runs, one neutral-description run, one `recommendationCacheFailure` run),
selected and labelled **before any production RCA pipeline code was written**. `manifest.json`
pins each case to the SHA-256 of its evidence rows in `eval/archive/aegis_demo_db_export.json.gz`.

Purpose: the production pipeline's scenario benchmark is authored by the same engineering effort
that builds the pipeline, so it can grade itself favourably. This set is the independent check.
Rules: evaluation only; nothing is tuned against it. Known limitations are listed in the manifest
(no span status, evidence lost to the research engine's ID collisions, windows not aligned to
fault onset, labels assigned from scenario definitions rather than by an independent human).
