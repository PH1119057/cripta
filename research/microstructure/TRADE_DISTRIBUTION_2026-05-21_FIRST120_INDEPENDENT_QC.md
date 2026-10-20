# CRIPTA — №27/#28 May21 2-hour extended primary vs Decimal independent

**CHECKED HERE, RESEARCH RESULT / NOT CANON / NOT READY.** Source of truth GitHub main was `bd40eb4a561301625a4a34ac2870cb8032006993` at pre-read. Date `2026-05-21`, UTC 00:00–02:00, five symbols. Server resources prior: load 5.93/5.96/5.98, RAM available 2384 MiB, four OLD90 replay workers still running. Read-only calls as `cripta` kept narrow; all ten runs (primary and independent, each of five) returned rc 0.

Independent source: GitHub `8aef5a39074998cfa5e5a175eaeec8ae3f07fa2b` `research/microstructure/experiments/trade_distribution_first_hour_decimal_qc_v1.py` fetched by exact commit and executed with `--minutes 120`. Script internal status label 'FIRST_HOUR' is legacy text; parameter and evidence explicitly **120 minutes**.

Primary source: server operational mirror `/srv/cripta/source_checkout/research/microstructure/experiments/trade_distribution_v1.py` with `--minutes 120`; **installed/mirror commit not independently attested**, so source identity to remote main is NOT VERIFIED.

| Symbol | fills | Buy | Sell | positive gap median (Decimal ms) | tie ratio | P95 ex-post USDT | P99 ex-post USDT | notional ≥P99 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| APTUSDT | 5941 | 2893 | 3048 | 1055.6000 | 0.6594276094276094276 | 469.70517 | 952.522522 | 0.1697565415626327877 |
| INJUSDT | 15300 | 8260 | 7040 | 243.5000 | 0.7140989607163866919 | 463.7678 | 1498.4684 | 0.2765467315989582048 |
| DOTUSDT | 13716 | 7114 | 6602 | 291.0500 | 0.7525337222019686475 | 391.313 | 866.98268 | 0.2086818059596641142 |
| LTCUSDT | 4530 | 2290 | 2240 | 2315.8000 | 0.7573415765069551777 | 1082.2 | 2726.2599999999998 | 0.3243852873983046894 |
| ARBUSDT | 20880 | 11350 | 9530 | 72.1000 | 0.7562622730973705637 | 420.79279099999997 | 958.368162 | 0.3044598614189898745 |

**Independent QC:** for all 5: Buy+Sell=fills, primary and Decimal independently agree in fill counts, side counts, p95/p99 size and volume fraction at float precision; median positive gaps and tied timestamp fraction agree to float precision, not byte-for-byte. No persisted event-level hashes. **No source ingestion/available-after proof.** Ex-post p95/p99 is descriptive only, never a causal feature. Neither this two-hour check nor previously completed first-hour check is full requested-day/full research period.

Unrelated `job_a6cbd10ed884` became ORPHANED in SentinelX notification after reconnect: no returned job outcome. Independently observed four OS replay workers alive; not permission to infer completed job. No job restart, service changes, DB/Strategy/LIVE mutation.

**Next gate:** source raw immutable SHA256 and exact mirror loaded source hash, time-event boundary differential corpus, per-fill QC and owner-selected period. Numeric §21 READY not granted.
