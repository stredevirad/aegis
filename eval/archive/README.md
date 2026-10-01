# Research run archive

`aegis_demo_db_export.json.gz` is a lossless JSON export of `aegis_demo.db`, the local SQLite file
that holds every live research run recorded during Phases 2–3 (18 incidents, 38 hypotheses,
1,184 evidence rows, 278 events, 18 recommendations), including the runs cited in
`docs/final-results.md` (for example `inc_fault_1787558017` and `inc_fault_1787558379`).

The database file itself is gitignored (`*.db`), so before this export the research record existed
on one machine only. The export records the SHA-256 of the source file it was taken from.

Caveat: the research repository keyed evidence by global IDs with `INSERT OR REPLACE`
(audit finding DATA-01), so evidence that two runs observed in common is attributed to the later
run in this record. Per-run evidence counts in the docs were taken at run time and may therefore
exceed the rows attributed to a run here.

Load it with:

```python
import gzip, json
data = json.load(gzip.open("eval/archive/aegis_demo_db_export.json.gz", "rt", encoding="utf-8"))
data["tables"]["incidents"]
```
