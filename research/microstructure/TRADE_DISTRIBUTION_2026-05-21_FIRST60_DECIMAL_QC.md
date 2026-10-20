# CRIPTA — Independent Decimal QC, first 60m 2026-05-21 (#27/#28)

**RESEARCH_RESULT / NOT CANON / NOT RESEARCH-READY.** Exact GitHub main HEAD before execution `1fcb6daca66e713264f920f456487748ca618cd2`. QC source committed `8aef5a39074998cfa5e5a175eaeec8ae3f07fa2b` at `research/microstructure/experiments/trade_distribution_first_hour_decimal_qc_v1.py`, fetched from this immutable raw GitHub commit and executed read-only as `cripta`; no host filesystem writes.

Scope: Bybit public_trades `2026-05-21 00:00–01:00 UTC`, APTUSDT, INJUSDT, DOTUSDT, LTCUSDT, ARBUSDT. All five bounded calls rc=0; Decimal event times, price-notional and positive gaps; check against earlier primary `trade_distribution_v1.py` first-hour reports (May21 file `TRADE_DISTRIBUTION_2026-05-21_FIRST60_FIVE_SYMBOLS.md`).

| Coin | fills | buy | sell | median positive ms (Decimal) | adjacent same-timestamp share | ex-post fill P95 USDT | P99 USDT | volume share fills >= P99 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| APT | 2875 | 1486 | 1389 | 1275.8000 | .650313152400835073 | 462.3091 | 922.372062 | .158828058373740031 |
| INJ | 8539 | 5055 | 3484 | 357.6500 | .720074959006793160 | 518.6831999999999 | 1495.5071999999998 | .226402367627487307 |
| DOT | 7034 | 3838 | 3196 | 279.2500 | .766244845727285653 | 449.12141999999994 | 916.22838 | .172310641657148271 |
| LTC | 2407 | 1178 | 1229 | 2574.0000 | .770573566084788030 | 1175.02 | 2296.275 | .217844475529983846 |
| ARB | 11438 | 6509 | 4929 | 79.3000 | .760688991868496983 | 465.93625599999996 | 1046.65464 | .295283321089269096 |

**QC:** five of five instrument count and side partitions equal earlier primary first-hour summary exactly; all ex-post size percentiles agree at printed precision; median gap and same-timestamp fractions agree within primary float precision. Distinct results *not* per-event hash equivalence or complete day/capture-time verification.

**Operational gate:** before calls, `uptime/free/ps` showed load 5.96/6.00/6.00, 2340 MiB available RAM, four concurrent OLD90 replay workers. User explicitly authorized using remaining compute; selected independent first-hour read-only bounded executions. Foreign job `job_a6cbd10ed884` was reported **orphaned** by SentinelX after reconnect, completion status UNKNOWN; OS snapshot showed four OLD90 workers active but cannot assign job result or claim completion. Not stopped/relaunched.

**Outstanding:** immutable raw SHA256, verified source mirror loaded commit for *primary* run, event received/available-after, full requested period and per-fill provenance. P95/P99 descriptive ex-post; do not use as intraday as-of threshold. No DB/service/Strategy/Execution/LIVE mutation.
