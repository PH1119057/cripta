# CRIPTA — verified 30-indicator research checkpoint index

**Updated from checked GitHub journal/source evidence on 2026-10-19, after #30 full-day 5/5.** Evidence, **NOT CANON**, no Strategy or LIVE policy. Authority: `PH1119057/cripta:main`. This file captures status at creation and is *not* an automated live dashboard.

Data cohort: Bybit APTUSDT, INJUSDT, DOTUSDT, LTCUSDT, ARBUSDT; UTC 2026-05-19 pilot day. Pilot-day successful computation is **not** OOS nor §21 RESEARCH-READY. Old 15/30 is a historical narrow research gate; no 150 instrument×factor pair attestation is implied. Use `docs/RESEARCH_COMPUTE_RULES_RU.md §21` for the required measurement passport and no-leak contract.

| # | Indicator | Latest verified stage | Remaining gate / limit |
|---:|---|---|---|
| 1 | RVOL | `HISTORICAL_PILOT_PASS` | Per-coin §21 re-attestation pending |
| 2 | Trade Rate | `HISTORICAL_PILOT_PASS` | Per-coin §21 re-attestation pending |
| 3 | Trade Rate Acceleration | `HISTORICAL_PILOT_PASS` | Per-coin §21 re-attestation pending |
| 4 | Large Trade Share | `HISTORICAL_PILOT_PASS` | Per-coin §21 re-attestation pending |
| 5 | Buy/Sell Delta | `HISTORICAL_PILOT_PASS` | Per-coin §21 re-attestation pending |
| 6 | CVD | `HISTORICAL_PILOT_PASS` | Per-coin §21 re-attestation pending |
| 7 | Delta Acceleration | `HISTORICAL_PILOT_PASS` | Per-coin §21 re-attestation pending |
| 8 | Trade Streaks | `HISTORICAL_PILOT_PASS` | Per-coin §21 re-attestation pending |
| 9 | Queue Imbalance | `HISTORICAL_PILOT_PASS` | Per-coin §21 re-attestation pending |
| 10 | Multi-Level OFI | `TOP1_OFI_FULL_DAY_5_OF_5 + TOP5_STOCK_DECOMPOSITION_5_OF_5` | Strict complete multi-level OFI still OPEN; rank reindexing must not be mislabeled |
| 11 | Order Book Depth | `HISTORICAL_PILOT_PASS` | Per-coin §21 re-attestation pending |
| 12 | Pulling Rate | `FULL_DAY_TRADE_BOOK_TIMING_SENSITIVITY_5_OF_5; TIMESTAMP_QC_10M_4_OF_5; BOUNDED_BOOK_CLOCK_SAMPLE_5_OF_5` | APT/INJ/DOT/ARB 10m timestamps checked, former LTC full-scan timeout remains unresolved; different first-500-message clock sample 5/5 checked, ts-cts ~1–112 ms and seq/u nondecreasing only within sample; still no cross-feed causal ordering or cancellation attribution (journal 8394cf0) |
| 13 | Depth Recovery | `SAME_PRICE_RECOVERY_FULL_DAY_5_OF_5` | Independent QC, temporal availability and per-pair passport remain |
| 14 | Price Impact | `FLOW_TO_NEXT_MINUTE_LABEL_FULL_DAY_5_OF_5` | Future label not causal feature; actual trade-level price impact OPEN |
| 15 | Effort / Result | `ONE_MINUTE_EFFORT_RESULT_PROXY_FULL_DAY_5_OF_5` | Further own-coin normalization and independent QC required |
| 16 | Absorption | `HIGH_FLOW_PRICE_RESISTANCE_PROXY_FULL_DAY_5_OF_5` | Not proved passive absorption or OOS |
| 17 | Exhaustion | `FIVE_MINUTE_DECAY_FUTURE_LABEL_FULL_DAY_5_OF_5` | Overlapping study anchors, no OOS |
| 18 | Short-Term Autocorrelation | `HISTORICAL_PILOT_PASS` | Per-coin §21 re-attestation pending |
| 19 | Range Compression | `PHYSICAL_QC_PASS_5_OF_5 + PASSPORTS_REGISTERED + SECOND_DAY_FULL_CALC_5_OF_5` | 2026-05-20 all five full 1440-row serializations hashed, primary reader only; independent second-day QC and §21 passport extension pending (journal commit 69b247a) |
| 20 | Local Extremes | `HISTORICAL_PILOT_PASS` | Per-coin §21 re-attestation pending |
| 21 | Sweep / Reclaim | `ORDERED_TRADE_PRINT_PROXY_FULL_DAY_5_OF_5` | Not proved book liquidity sweep; same timestamp ordering QC |
| 22 | VWAP / Volume Profile | `PHYSICAL_QC_PASS_5_OF_5 + PASSPORTS_REGISTERED + SECOND_DAY_FULL_CALC_5_OF_5` | 2026-05-20 all five full 1440-row serializations hashed, primary reader only; independent second-day QC and §21 passport extension pending (journal commit 69b247a) |
| 23 | OI Change | `HISTORICAL_SOURCE_UNVERIFIED_5_OF_5` | Bounded path search found no verified 2026-05-19 series; missing=NO_DATA |
| 24 | Liquidation Spikes | `PARTIAL_HOURLY_FILES_5_OF_5` | No verified full-day event completeness; alternate interpreter probe stopped by SentinelX command_not_allowed (2026-10-19) |
| 25 | Spot / Perpetual Divergence | `SPOT_SOURCE_UNVERIFIED_5_OF_5` | Bounded path search found no verified same-day Spot series |
| 26 | Cross-Venue Lead–Lag | `OWNER_DEFERRED_LAST` | Do not run until Bybit indicator work complete |
| 27 | Inter-Trade Duration | `HISTORICAL_PILOT_PASS` | Per-coin §21 re-attestation pending |
| 28 | Trade-Size Distribution | `HISTORICAL_PILOT_PASS` | Per-coin §21 re-attestation pending |
| 29 | Bid/Ask Spread | `HISTORICAL_PILOT_PASS` | Per-coin §21 re-attestation pending |
| 30 | Replenishment / Resiliency | `TOP5_BOOK_STOCK_RECOVERY_FULL_DAY_5_OF_5_CHECKED` | APT/INJ/DOT/LTC/ARB full-day results verified; strict same-order resilience OPEN. Evidence GitHub journal full-day #30 checkpoint 2026-10-19. |

## Evidence / checkpoints

- Main appended chronologically, including exact source commit and per-symbol counts after each indicator: `research/CRIPTA_RESEARCH_JOURNAL_2026-10-18.md`
- Per-instrument legacy audit and 10 #19/#22 QC pairs: `research/microstructure/measurement_audit_2026-10-18.md`
- #19/#22 explicit passports: `research/microstructure/passports/range_vwap_2026-05-19_v1.json`
- Script source implementations: `research/microstructure/experiments/`; source/research artifacts require documented QA before reuse.
- Unknown/missing data: `NO_DATA`, never inferred zero, especially #23 OI, #24 liquidations and #25 Spot.
- Per-coin independent calculations and OOS/fees remain future work. Never average raw base-asset OFI across coins or promote older thresholds.

## Latest bounded access stop — 2026-10-19

The five-coin historical source-file manifest for #24 is already published and remains valid. During optional deeper parquet content QC, a read-only attempt to invoke an alternate Python interpreter returned SentinelX `command_not_allowed` because its parser detected a shell `;` operator; the host execution was not demonstrated. No bypass, re-encoding, privileges changes or installation attempted. **HARD_STOP for this alternate-interpreter route**, not a claim that all server research access is unavailable. See exact journal entry. `#24` remains OPEN / file coverage verified / actual event completeness unverified. Continuing this blocked route requires an explicitly approved tool-policy path or an already authorized separate reader; do not improvise. No LIVE.
