# CRIPTA — verified 30-indicator research checkpoint index

**Updated from checked GitHub journal/source evidence on 2026-10-19, after #30 full-day 5/5.** Evidence, **NOT CANON**, no Strategy or LIVE policy. Authority: `PH1119057/cripta:main`. This file captures status at creation and is *not* an automated live dashboard.

Data cohort: Bybit APTUSDT, INJUSDT, DOTUSDT, LTCUSDT, ARBUSDT; UTC 2026-05-19 pilot day. Pilot-day successful computation is **not** OOS nor §21 RESEARCH-READY. Old 15/30 is a historical narrow research gate; no 150 instrument×factor pair attestation is implied. Use `docs/RESEARCH_COMPUTE_RULES_RU.md §21` for the required measurement passport and no-leak contract.

| # | Indicator | Latest verified stage | Remaining gate / limit |
|---:|---|---|---|
| 1 | RVOL | `HISTORICAL_PILOT_PASS + SECOND_DAY_CALC_5_OF_5` | 2026-05-20 all five physical calculations executed; independent row QC and per-coin §21 passports pending (journal a203f5f) |
| 2 | Trade Rate | `HISTORICAL_PILOT_PASS + SECOND_DAY_CALC_5_OF_5` | 2026-05-20 all five physical calculations executed; independent row QC and per-coin §21 passports pending (journal a203f5f) |
| 3 | Trade Rate Acceleration | `HISTORICAL_PILOT_PASS + SECOND_DAY_CALC_5_OF_5` | 2026-05-20 all five physical calculations executed; independent row QC and per-coin §21 passports pending (journal a203f5f) |
| 4 | Large Trade Share | `HISTORICAL_PILOT_PASS + SECOND_DAY_PHYSICAL_CALC_5_OF_5` | May 20 all five executed, no independent row equivalence/§21 passport; journal 3c75bb7 |
| 5 | Buy/Sell Delta | `HISTORICAL_PILOT_PASS + SECOND_DAY_CALC_5_OF_5` | 2026-05-20 all five physical calculations executed; independent row QC and per-coin §21 passports pending (journal a203f5f) |
| 6 | CVD | `HISTORICAL_PILOT_PASS + SECOND_DAY_CALC_5_OF_5` | 2026-05-20 all five physical calculations executed; independent row QC and per-coin §21 passports pending (journal a203f5f) |
| 7 | Delta Acceleration | `HISTORICAL_PILOT_PASS + SECOND_DAY_CALC_5_OF_5` | 2026-05-20 all five physical calculations executed; independent row QC and per-coin §21 passports pending (journal a203f5f) |
| 8 | Trade Streaks | `HISTORICAL_PILOT_PASS + SECOND_DAY_PHYSICAL_CALC_5_OF_5` | May 20 all five executed, no independent row equivalence/§21 passport; journal 3c75bb7 |
| 9 | Queue Imbalance | `FULL_DAY_BOOK_MINUTE_5_OF_5_PASS + INDEPENDENT_FULL_DAY_SUMMARY_QC_5_OF_5` | May 19 1440 minute states each coin, snapshots+delta count reconciled; single-reader ±5bp depth/imbalance and top spread physical measurements, independent full-day rounded medians/coverage/counts agree (journal 2043539); full 1440-row independent QC and §21 passport pending (journal 5aaf62b) |
| 10 | Multi-Level OFI | `TOP1_OFI_FULL_DAY_5_OF_5 + TOP5_STOCK_DECOMPOSITION_5_OF_5; ALGEBRAIC_TOP1_VS_RANK0_AUDIT` | Immutable historical top1 vs current rank0 formula equivalent only conditional on common events/depth; Decimal/float differences, deeper ranks reindex, and no numerical replay. Strict price-identity multi-level OFI OPEN (journal 753ba1e, source audit 76701f9) |
| 11 | Order Book Depth | `FULL_DAY_BOOK_MINUTE_5_OF_5_PASS + INDEPENDENT_FULL_DAY_SUMMARY_QC_5_OF_5` | May 19 1440 minute states each coin, snapshots+delta count reconciled; single-reader ±5bp depth/imbalance and top spread physical measurements, independent full-day rounded medians/coverage/counts agree (journal 2043539); full 1440-row independent QC and §21 passport pending (journal 5aaf62b) |
| 12 | Pulling Rate | `FULL_DAY_TRADE_BOOK_TIMING_SENSITIVITY_5_OF_5; TIMESTAMP_QC_10M_4_OF_5; BOUNDED_BOOK_CLOCK_SAMPLE_5_OF_5` | APT/INJ/DOT/ARB 10m timestamps checked, former LTC full-scan timeout remains unresolved; different first-500-message clock sample 5/5 checked, ts-cts ~1–112 ms and seq/u nondecreasing only within sample; still no cross-feed causal ordering or cancellation attribution (journal 8394cf0) |
| 13 | Depth Recovery | `SAME_PRICE_RECOVERY_FULL_DAY_5_OF_5 + SECOND_DAY_CALC_5_OF_5` | May20 5/5 full-day, bid/ask episode partitions PASS, source v2 at 6359ada, journal 46afd50; independent QC, source time availability and per-pair §21 passport OPEN |
| 14 | Price Impact | `FLOW_TO_NEXT_MINUTE_LABEL_FULL_DAY_5_OF_5 + SECOND_DAY_PARTIAL_4_OF_5` | May20 APT/INJ/DOT/LTC 4/5 and partition QC, ARB NOT EXECUTED after OpenAI upstream block; future outcome only, not trade impact or causal feature; journal b17a582 |
| 15 | Effort / Result | `ONE_MINUTE_EFFORT_RESULT_PROXY_FULL_DAY_5_OF_5 + SECOND_DAY_CALC_5_OF_5` | May20 1440 rows each with explicit empty minutes; per-coin turnover-normalized median and p90, source v2 10fe631, journal 7b3be4c; independent per-row QC, §21 passport/OOS still OPEN |
| 16 | Absorption | `SECOND_DAY_PHYSICAL_PROXY_5_OF_5` | May20 5/5: prior60 baseline, against-flow proxy 15/26/24/20/23; first v2 output date label stale (fixed in 42cfa21, not rerun); see journal 15e4d54; independent per-row QC/§21/OOS OPEN |
| 17 | Exhaustion | `SECOND_DAY_PHYSICAL_PROXY_5_OF_5` | May20 5/5: 5m decay anchors 55/46/46/55/50; future reversal separate noncausal label; see journal 15e4d54; independent per-row QC/§21/OOS OPEN |
| 18 | Short-Term Autocorrelation | `HISTORICAL_PILOT_PASS + SECOND_DAY_PARTIAL_3_OF_5` | APT/INJ/DOT May 20 checked; LTC/ARB NOT EXECUTED after OpenAI upstream tool-security block; HARD_STOP route; see journal 23a4de7 |
| 19 | Range Compression | `SECOND_DAY_ALL_ROWS_INDEPENDENT_HASH_5_OF_5 + RAW_SHA256_QC_5_OF_5` | 2026-05-20 exact source gz SHA256, size, trade order and notional checks registered; 1440-row independent exact-hash 5/5 PASS; §21.6 formal per-pair READY gate still pending (journal bae0826) |
| 20 | Local Extremes | `HISTORICAL_PILOT_PASS + SECOND_DAY_PARTIAL_3_OF_5` | APT/INJ/DOT May 20 checked; LTC/ARB NOT EXECUTED after OpenAI upstream tool-security block; HARD_STOP route; see journal 23a4de7 |
| 21 | Sweep / Reclaim | `ORDERED_TRADE_PRINT_PROXY_FULL_DAY_5_OF_5 + SECOND_DAY_CALC_5_OF_5` | May20 prior60 print sweep/reclaim candidate: eligible 1380/1380/1193/872/1380 by APT/INJ/DOT/LTC/ARB; reclaim≤sweep QC PASS 5/5; not book liquidity sweep, tie ordering unresolved, §21/OOS OPEN (journal ef20ee3) |
| 22 | VWAP / Volume Profile | `SECOND_DAY_ALL_ROWS_INDEPENDENT_HASH_5_OF_5 + RAW_SHA256_QC_5_OF_5` | 2026-05-20 exact source gz SHA256, size, trade order and notional checks registered; 1440-row independent exact-hash 5/5 PASS; §21.6 formal per-pair READY gate still pending (journal bae0826) |
| 23 | OI Change | `HISTORICAL_SOURCE_UNVERIFIED_5_OF_5` | Bounded path search found no verified 2026-05-19 series; missing=NO_DATA |
| 24 | Liquidation Spikes | `PARTIAL_HOURLY_FILES_5_OF_5` | No verified full-day event completeness; alternate interpreter probe stopped by SentinelX command_not_allowed (2026-10-19) |
| 25 | Spot / Perpetual Divergence | `SPOT_SOURCE_UNVERIFIED_5_OF_5` | Bounded path search found no verified same-day Spot series |
| 26 | Cross-Venue Lead–Lag | `OWNER_DEFERRED_LAST` | Do not run until Bybit indicator work complete |
| 27 | Inter-Trade Duration | `HISTORICAL_PILOT_PASS + SECOND_DAY_PHYSICAL_CALC_5_OF_5` | May 20 all five executed, no independent row equivalence/§21 passport; journal 3c75bb7 |
| 28 | Trade-Size Distribution | `HISTORICAL_PILOT_PASS + SECOND_DAY_PHYSICAL_CALC_5_OF_5` | May 20 all five executed, no independent row equivalence/§21 passport; journal 3c75bb7 |
| 29 | Bid/Ask Spread | `FULL_DAY_BOOK_MINUTE_5_OF_5_PASS + INDEPENDENT_FULL_DAY_SUMMARY_QC_5_OF_5` | May 19 1440 minute states each coin, snapshots+delta count reconciled; single-reader ±5bp depth/imbalance and top spread physical measurements, independent full-day rounded medians/coverage/counts agree (journal 2043539); full 1440-row independent QC and §21 passport pending (journal 5aaf62b) |
| 30 | Replenishment / Resiliency | `TOP5_BOOK_STOCK_RECOVERY_FULL_DAY_5_OF_5_CHECKED + SECOND_DAY_VERIFIED_3_OF_5` | May20 APT/DOT/LTC completed with partition QC PASS. INJ job_e2201594f884 timeout (-1), outcome UNKNOWN; ARB job_22c8f6bba901 agent non-response, outcome UNKNOWN. No retries. Strict same-order resiliency OPEN; journal 5089f50 |

## §21.6 evidence-gate update (2026-10-19)

Checked main passport for #19/#22: exact raw SHA256 and independent full-minute hashes already present for 5/5, but per-pair formal §21.6 and available-after/source-capture, persistent dataset/cube QC still OPEN. See `research/microstructure/CHECKLIST_EXECUTION_2026-10-19.md`. No blocked server route retried. This is documentary review only.

## Execution checklist

- [30-indicator execution checklist](CHECKLIST_EXECUTION_2026-10-19.md) (GitHub-committed, owner scope, #26 last, active route hard stops and unclosed gates).

## Evidence / checkpoints

- Main appended chronologically, including exact source commit and per-symbol counts after each indicator: `research/CRIPTA_RESEARCH_JOURNAL_2026-10-18.md`
- Per-instrument legacy audit and 10 #19/#22 QC pairs: `research/microstructure/measurement_audit_2026-10-18.md`
- #19/#22 explicit passports: `research/microstructure/passports/range_vwap_2026-05-19_v1.json`
- Script source implementations: `research/microstructure/experiments/`; source/research artifacts require documented QA before reuse.
- Unknown/missing data: `NO_DATA`, never inferred zero, especially #23 OI, #24 liquidations and #25 Spot.
- Per-coin independent calculations and OOS/fees remain future work. Never average raw base-asset OFI across coins or promote older thresholds.

## Latest bounded access stop — 2026-10-19

The five-coin historical source-file manifest for #24 is already published and remains valid. During optional deeper parquet content QC, a read-only attempt to invoke an alternate Python interpreter returned SentinelX `command_not_allowed` because its parser detected a shell `;` operator; the host execution was not demonstrated. No bypass, re-encoding, privileges changes or installation attempted. **HARD_STOP for this alternate-interpreter route**, not a claim that all server research access is unavailable. See exact journal entry. `#24` remains OPEN / file coverage verified / actual event completeness unverified. Continuing this blocked route requires an explicitly approved tool-policy path or an already authorized separate reader; do not improvise. No LIVE.


## Current hard stop — 2026-10-19 later stage

During #18/#20 second-day extension, 3/5 read-only runs succeeded (APT/INJ/DOT) then OpenAI upstream tool security blocked the next execution. No SentinelX server-side denial was proven. No repeated/obfuscated call or actor change. The failed route is stopped; owner decision required for an authorized alternative. Checkpoint: research journal commit `23a4de7d6a2aba83618261c88d16076971b8766c`. #9/#11/#29 full-day independent rounded summary 5/5 remains confirmed. No LIVE.


## 2026-10-19 source identity audit

Nine-instrument-indicator family audit for #10/#12–17/#21/#30, source identity and strict-vs-proxy boundaries: `research/microstructure/passports/proxy_identity_gate_audit_2026-10-19_v1.json`. Historical #10 top1 reader recovered from exact immutable commit; current-main equivalent NOT established. All formal §21.6 gates remain distinct from pilot measurement. No blocked server execution retried.
