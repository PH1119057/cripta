# CRIPTA — verified 30-indicator research checkpoint index

**Updated from checked GitHub journal/source evidence on 2026-10-19, after #30 full-day 5/5.** Evidence, **NOT CANON**, no Strategy or LIVE policy. Authority: `PH1119057/cripta:main`. This file captures status at creation and is *not* an automated live dashboard.

Data cohort: Bybit APTUSDT, INJUSDT, DOTUSDT, LTCUSDT, ARBUSDT; UTC 2026-05-19 pilot day. Pilot-day successful computation is **not** OOS nor §21 RESEARCH-READY. Old 15/30 is a historical narrow research gate; no 150 instrument×factor pair attestation is implied. Use `docs/RESEARCH_COMPUTE_RULES_RU.md §21` for the required measurement passport and no-leak contract.

| # | Indicator | Latest verified stage | Remaining gate / limit |
|---:|---|---|---|
| 1 | RVOL | `V1_FULL_ROW_APT_FAIL + CANDIDATE_V2_FULL_ROW_5_OF_5_PASS` | May20 v1 exact minute QC 4/5; APT three Buy fills at 23:26:59.9995 mis-rounded. Separate candidate v2 (timestamp floor) full 1440 rows×8 fields 5/5 PASS, zero mismatches under tolerance; journal b81280e; §21 per-pair READY OPEN |
| 2 | Trade Rate | `V1_FULL_ROW_APT_FAIL + CANDIDATE_V2_FULL_ROW_5_OF_5_PASS` | May20 v1 exact minute QC 4/5; APT three Buy fills at 23:26:59.9995 mis-rounded. Separate candidate v2 (timestamp floor) full 1440 rows×8 fields 5/5 PASS, zero mismatches under tolerance; journal b81280e; §21 per-pair READY OPEN |
| 3 | Trade Rate Acceleration | `V1_FULL_ROW_APT_FAIL + CANDIDATE_V2_FULL_ROW_5_OF_5_PASS` | May20 v1 exact minute QC 4/5; APT three Buy fills at 23:26:59.9995 mis-rounded. Separate candidate v2 (timestamp floor) full 1440 rows×8 fields 5/5 PASS, zero mismatches under tolerance; journal b81280e; §21 per-pair READY OPEN |
| 4 | Large Trade Share | `HISTORICAL_PILOT_PASS + SECOND_DAY_PHYSICAL_CALC_5_OF_5 + INDEPENDENT_DECIMAL_SUMMARY_QC_5_OF_5` | May 20 all five executed, independent Decimal full-day cohort aggregate (large-share median/p90, streak counts/percentiles) agrees 5/5; per-minute row QC and §21 passport remain OPEN; journal 01b5e87 |
| 5 | Buy/Sell Delta | `V1_FULL_ROW_APT_FAIL + CANDIDATE_V2_FULL_ROW_5_OF_5_PASS` | May20 v1 exact minute QC 4/5; APT three Buy fills at 23:26:59.9995 mis-rounded. Separate candidate v2 (timestamp floor) full 1440 rows×8 fields 5/5 PASS, zero mismatches under tolerance; journal b81280e; §21 per-pair READY OPEN |
| 6 | CVD | `V1_FULL_ROW_APT_FAIL + CANDIDATE_V2_FULL_ROW_5_OF_5_PASS` | May20 v1 exact minute QC 4/5; APT three Buy fills at 23:26:59.9995 mis-rounded. Separate candidate v2 (timestamp floor) full 1440 rows×8 fields 5/5 PASS, zero mismatches under tolerance; journal b81280e; §21 per-pair READY OPEN |
| 7 | Delta Acceleration | `V1_FULL_ROW_APT_FAIL + CANDIDATE_V2_FULL_ROW_5_OF_5_PASS` | May20 v1 exact minute QC 4/5; APT three Buy fills at 23:26:59.9995 mis-rounded. Separate candidate v2 (timestamp floor) full 1440 rows×8 fields 5/5 PASS, zero mismatches under tolerance; journal b81280e; §21 per-pair READY OPEN |
| 8 | Trade Streaks | `HISTORICAL_PILOT_PASS + SECOND_DAY_PHYSICAL_CALC_5_OF_5 + INDEPENDENT_DECIMAL_SUMMARY_QC_5_OF_5` | May 20 all five executed, independent Decimal full-day cohort aggregate (large-share median/p90, streak counts/percentiles) agrees 5/5; per-minute row QC and §21 passport remain OPEN; journal 01b5e87 |
| 9 | Queue Imbalance | `FULL_DAY_BOOK_MINUTE_5_OF_5_PASS + INDEPENDENT_FULL_DAY_SUMMARY_QC_5_OF_5` | May 19 1440 minute states each coin, snapshots+delta count reconciled; single-reader ±5bp depth/imbalance and top spread physical measurements, independent full-day rounded medians/coverage/counts agree (journal 2043539); full 1440-row independent reader committed 8dfa76d, APT job_cb166e3a8aa1 agent no-response/outcome UNKNOWN journal 9be5812; no rerun, §21 passport pending (journal 5aaf62b) |
| 10 | Multi-Level OFI | `TOP1_OFI_FULL_DAY_5_OF_5 + TOP5_STOCK_DECOMPOSITION_5_OF_5; ALGEBRAIC_TOP1_VS_RANK0_AUDIT` | Immutable historical top1 vs current rank0 formula equivalent only conditional on common events/depth; Decimal/float differences, deeper ranks reindex, and no numerical replay. Strict price-identity multi-level OFI OPEN (journal 753ba1e, source audit 76701f9) |
| 11 | Order Book Depth | `FULL_DAY_BOOK_MINUTE_5_OF_5_PASS + INDEPENDENT_FULL_DAY_SUMMARY_QC_5_OF_5` | May 19 1440 minute states each coin, snapshots+delta count reconciled; single-reader ±5bp depth/imbalance and top spread physical measurements, independent full-day rounded medians/coverage/counts agree (journal 2043539); full 1440-row independent reader committed 8dfa76d, APT job_cb166e3a8aa1 agent no-response/outcome UNKNOWN journal 9be5812; no rerun, §21 passport pending (journal 5aaf62b) |
| 12 | Pulling Rate | `FULL_DAY_TRADE_BOOK_TIMING_SENSITIVITY_5_OF_5; BOUNDED_BOOK_CLOCK_SAMPLE_5_OF_5_ON_TWO_DAYS` | May20 trade/book first500 records per symbol 5/5, book seq/update id monotonic and ts-cts observed 1–123ms; not full-day clock/causal crossing. Historical LTC long scan timeout still unresolved; cancellation attribution OPEN. Journal 6cc3322 |
| 13 | Depth Recovery | `SAME_PRICE_RECOVERY_DAY2_5_OF_5 + RAW_ENDPOINT_AGE_QC_5_OF_5_FINDING` | May20 independently repeated source event inventories 5/5; 705 still-open book-price episodes at UTC day end, including **2 older than 60s** (LTC Bid 1, ARB Bid 1); main v2 classifies both open. All partitions PASS but timer endpoint semantics unresolved pending source continuity/owner policy; journal cbb0cb9 |
| 14 | Price Impact | `FLOW_TO_NEXT_MINUTE_LABEL_FULL_DAY_5_OF_5 + SECOND_DAY_PARTIAL_4_OF_5` | May20 APT/INJ/DOT/LTC 4/5 and partition QC, ARB NOT EXECUTED after OpenAI upstream block; future outcome only, not trade impact or causal feature; journal b17a582 |
| 15 | Effort / Result | `SECOND_DAY_CALC_5_OF_5 + INDEPENDENT_SUMMARY_5_OF_5 + TWO_PATH_ALL_MINUTE_QC_5_OF_5` | May20 5/5 independent summary and two-path algebraic formula/aggregation checks across all eligible minute rows 0 mismatches, source 3b6d9ea and journal c23cd55; no direct primary all-row equality claim. Source timing / §21 passport / OOS OPEN |
| 16 | Absorption | `SECOND_DAY_PHYSICAL_PROXY_5_OF_5 + INDEPENDENT_DECIMAL_SUMMARY_QC_5_OF_5` | May20 independent prior60 flow threshold and against/aligned/flat/unavailable count partitions exactly match primary 5/5; passive order absorption unproven; full per-minute QC/§21/OOS OPEN (journal fbb3068) |
| 17 | Exhaustion | `SECOND_DAY_PHYSICAL_PROXY_5_OF_5 + INDEPENDENT_SUMMARY_QC_5_OF_5` | May20 independent reconstructed five-minute decay/nondecay anchors, median flow ratios and next-block reversal/continuation future-label partitions agree 5/5 (journal 8378ed6). Future outcomes not causal features, row §21/OOS still OPEN |
| 18 | Short-Term Autocorrelation | `HISTORICAL_PILOT_PASS + SECOND_DAY_PARTIAL_3_OF_5` | APT/INJ/DOT May 20 checked; LTC/ARB NOT EXECUTED after OpenAI upstream tool-security block; HARD_STOP route; see journal 23a4de7 |
| 19 | Range Compression | `SECOND_DAY_ALL_ROWS_INDEPENDENT_HASH_5_OF_5 + RAW_SHA256_QC_5_OF_5` | 2026-05-20 exact source gz SHA256, size, trade order and notional checks registered; 1440-row independent exact-hash 5/5 PASS; §21.6 formal per-pair READY gate still pending (journal bae0826) |
| 20 | Local Extremes | `HISTORICAL_PILOT_PASS + SECOND_DAY_PARTIAL_3_OF_5` | APT/INJ/DOT May 20 checked; LTC/ARB NOT EXECUTED after OpenAI upstream tool-security block; HARD_STOP route; see journal 23a4de7 |
| 21 | Sweep / Reclaim | `SECOND_DAY_PRINT_PROXY_5_OF_5 + INDEPENDENT_SUMMARY_QC_5_OF_5` | May20 independent prior60 print-extrema crossing/reclaim count and missing-window partitions agree on all five coins; not a verified book liquidity sweep, same-timestamp tie ordering unresolved; event-level QC/§21/OOS OPEN (journal 8ee1c7a) |
| 22 | VWAP / Volume Profile | `SECOND_DAY_ALL_ROWS_INDEPENDENT_HASH_5_OF_5 + RAW_SHA256_QC_5_OF_5` | 2026-05-20 exact source gz SHA256, size, trade order and notional checks registered; 1440-row independent exact-hash 5/5 PASS; §21.6 formal per-pair READY gate still pending (journal bae0826) |
| 23 | OI Change | `SHALLOW_SOURCE_INVENTORY_SECOND_DAY_5_OF_5_NO_CANDIDATES` | May20 shallow known roots 5/5 no OI candidates, not exhaustive source absence; exact-day OI unverified; NO_DATA ≠ zero. Journal 31f79a8 |
| 24 | Liquidation Spikes | `PARTIAL_HOURLY_FILES_5_OF_5` | No verified full-day event completeness; alternate interpreter probe stopped by SentinelX command_not_allowed (2026-10-19) |
| 25 | Spot / Perpetual Divergence | `SPOT_SOURCE_UNVERIFIED_5_OF_5; SECOND_DAY_PROBE_PREPARED_NOT_EXECUTED` | May20 probe source v2 exists but first run blocked upstream OpenAI, 0/5; no retry, route HARD_STOP, source unverified (journal 31f79a8) |
| 26 | Cross-Venue Lead–Lag | `OWNER_DEFERRED_LAST` | Do not run until Bybit indicator work complete |
| 27 | Inter-Trade Duration | `HISTORICAL_PILOT_PASS + SECOND_DAY_PHYSICAL_CALC_5_OF_5 + INDEPENDENT_DECIMAL_DISTRIBUTION_QC_5_OF_5` | May 20 all five executed, independent fill inventory, inter-fill median, ex-post p95/p99 and volume share reproduced 5/5 (journal 4fd8777); per-fill hash/§21 passport pending |
| 28 | Trade-Size Distribution | `HISTORICAL_PILOT_PASS + SECOND_DAY_PHYSICAL_CALC_5_OF_5 + INDEPENDENT_DECIMAL_DISTRIBUTION_QC_5_OF_5` | May 20 all five executed, independent fill inventory, inter-fill median, ex-post p95/p99 and volume share reproduced 5/5 (journal 4fd8777); per-fill hash/§21 passport pending |
| 29 | Bid/Ask Spread | `FULL_DAY_BOOK_MINUTE_5_OF_5_PASS + INDEPENDENT_FULL_DAY_SUMMARY_QC_5_OF_5` | May 19 1440 minute states each coin, snapshots+delta count reconciled; single-reader ±5bp depth/imbalance and top spread physical measurements, independent full-day rounded medians/coverage/counts agree (journal 2043539); full 1440-row independent reader committed 8dfa76d, APT job_cb166e3a8aa1 agent no-response/outcome UNKNOWN journal 9be5812; no rerun, §21 passport pending (journal 5aaf62b) |
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
