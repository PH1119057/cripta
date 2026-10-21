# CRIPTA — проход 4/5: Bybit OI, liquidations, Spot/Perp и deferred Cross-Venue

**2026-10-21 CHECKED HERE. RESEARCH EVIDENCE; NOT CANON; NOT RESEARCH-READY.** Authority `PH1119057/cripta:main`, pre-publication HEAD `c48d126cc851080e80cf75c0f318503f1bc896b2`. Causal / source identity requirements `docs/RESEARCH_COMPUTE_RULES_RU.md §20–21`; same-actor read-only route `docs/DEVELOPMENT_RELEASE_RULES_RU.md §19.5–19.6`.

## 1. Critical correction to old source-gap registry

`FIRST_PASS_MAP_30_INDICATORS_2026-10-20.md`, `INDICATOR_PROGRESS_2026-10-19.md`, and `FIRST_PASS_SOURCE_GAPS_2026-10-20.md` describe #23/#25 as shallow `SOURCE_GAP`, but they **do not incorporate** a newer within-file 2026-10-18 recovery section at the end of `research/microstructure/coverage_2026-05-19.md`: `DIRECT BYBIT ONE-DAY DATA RECOVERY VERIFIED (supersedes earlier UNVERIFIED rows)`. Never use the old shallow-negative finding to assert there was no historical May19 source.

| # | Historical single-day source discovery (existing CHECKED HERE 2026-10-18) | Current precise status |
|---|---|---|
| 23 OI Change | Official Bybit GET market open-interest endpoint, `category=linear`, `intervalTime=5min`, `startTime=1779148800000`, `endTime=1779235199999`, cursor pagination, `retCode=0` on APT/INJ/DOT/LTC/ARB; 288 unique grid timestamps per symbol, 1440 total; 00:00–23:55 UTC May19 | `SOURCE_RESPONSES_VERIFIED_SINGLE_DAY_5_OF_5_UNSNAPSHOTTED`; payload values/binary hashes not retained, publication/as-of meaning not proven, full research period absent, **NO DERIVED OI CHANGE READY** |
| 24 Liquidation Spikes | CryptoHFTData Bybit observed liquidation files, historical 29/120 nonempty symbol-hour files for May19; five processed CSV.GZ archives decoded, 101 records in 29 hours | `PARTIAL_OBSERVED_EVENTS`, 91/120 hours **UNKNOWN**, never zero; source capture/continuity not proved, **NO DERIVED SPIKE READY** |
| 25 Spot/Perp Divergence | Five official Bybit public Spot May19 GZIP/CSV source URLs returned HEAD 200 and were decoded in-memory; APT 10074, INJ 24221, DOT 8580, LTC 16860, ARB 10099 spot fills, **69834 total**, 24/24 hours with ≥1 Spot trade per coin | `SPOT_SOURCE_RESPONSES_VERIFIED_SINGLE_DAY_5_OF_5_UNSNAPSHOTTED`; raw bytes and hashes not retained, Spot/Perp synchronized as-of price series/basis never validated; **NO DERIVED DIVERGENCE READY** |
| 26 Cross-Venue Lead–Lag | Owner explicitly deferred other venues until last, per §21.1/§21.7 | `OWNER_DEFERRED_LAST / NOT EXECUTED / 0_OF_5`; do not run before Bybit readiness/source gates |

**Evidence caveat:** the May19 Bybit API and Spot response figures above come from a previously executed source read **documented in GitHub**, not a fresh API re-fetch in this pass. Existence and one-day historical response coverage ≠ immutable raw snapshot, exchange correctness or verified causal publication time. `mayak_dispatcher_market_v1` or current live Spot feeds are not substitutes for historical immutable May19 data.

## 2. NEW direct source-provenance confirmation — processed liquidation CSV.GZ

One read-only `sudo -u cripta python3` batch under the already approved actor/rail; `returncode=0`. All five archived processed files at `/data/cripta/research/runs/strategy_full20_liq_20260922/liquidations/{SYMBOL}.csv.gz` independently re-read with `gzip/csv` and sha256 over the exact gzip bytes. Raw header physically confirmed on APT: `observed_timestamp_us,trade_timestamp_us,symbol,liquidated_position_side,price,size,order_status,source_path`. May19 window uses `observed_timestamp_us ∈ [1779148800000000,1779235200000000)` integer microseconds.

| Symbol | May19 records | Hours with records /24 | Source paths represented | Compressed bytes | SHA256 of processed gzip |
|---|---:|---:|---:|---:|---|
| APTUSDT | 11 | 7 | 7 | 88046 | `95d17cf9653951ea0b7663d4b4fb3a092a678f05c7a4172ab748ec23f088a2e3` |
| INJUSDT | 74 | 15 | 15 | 99934 | `08caaba003b5c18307b8b3241c72358b703dcf6c7099cb6230aabe37d9730d46` |
| DOTUSDT | 4 | 1 | 1 | 87095 | `6f4734d314619a734b7339ef90ece8435e5f272adc76134de57016067aa9939b` |
| LTCUSDT | 5 | 2 | 2 | 70545 | `614c90953cf543a0d86ac20e9f4bfbd4506b9240d44aa91d6380c17189501077` |
| ARBUSDT | 7 | 4 | 4 | 170690 | `bc03531e39ea92378257936eee5d5ff6c08d89ee5a479dca7b23e2ffde53c636` |
| **TOTAL** | **101** | **29/120 symbol-hours** | **29 represented** | — | — |

Identity mismatches `symbol` in filtered May19 rows = **0 for all five**. Hourly distributions exactly agree with `coverage_2026-05-19.md`: APT hours 00/09/11/14/16/17/18; INJ 00/01/02/03/04/05/09/10/11/14/16/17/18/19/22; DOT 14; LTC 00/14; ARB 14/16/22/23. `observed_timestamp_us` is a named timestamp in *processed provider-derived data*; not proof of independent local collector receipt. SHA256 covers processed gzip, **not** provenance-complete raw CryptoHFTData hourly parquet or proof that missing hours had zero liquidations.

## 3. Pass disposition — without false formal promotion

- `PASS4_SOURCE_EVIDENCE_REVIEW=COMPLETE_WITH_GATES_OPEN`: #23 and #25 one-day real-source discovery exists; previous broad `SOURCE_GAP` labeling must be qualified as missing **immutable historical snapshots and derivation**, not proof of unavailable raw external source. #24 101 May19 provider-observed rows confirmed again, 91 unknown symbol-hours. #26 owner deferred.
- `PASS4_RESEARCH_READY=0/20` across 4 IDs×5 instruments, including deferred #26.
- Before genuine #23/#25 measurements: versioned safe Bybit source reader, exact immutable OI response & Spot archive snapshots/hashes, timestamp/event/observed/available-after contract, validated time alignment, complete owner-selected range per coin, independent QC and source manifest. For #24: source provider continuity and explicit missingness for each hour; no imputation of absent file as zero.
- No reruns of previously upstream-blocked probe or alternate interpreter. No new historical Bybit API call in this pass, no source snapshots written; no rights/config/service/DB/Strategy/LIVE modifications.
- Cross-venue #26 remains **last**; completion of documentation review is not owner approval to compute it now.

See `research/microstructure/coverage_2026-05-19.md` last section for the official source API/URL evidence and `docs/RESEARCH_COMPUTE_RULES_RU.md §21.6` for all mandatory per-pair gates.
