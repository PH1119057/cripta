# CRIPTA — per-instrument indicator measurement audit (2026-10-18)

Status: **RESEARCH AUDIT / NOT CANON / NO NEW READINESS PROMOTION**. Canon: `docs/RESEARCH_COMPUTE_RULES_RU.md §21`. Source: `research/CRIPTA_RESEARCH_JOURNAL_2026-10-18.md` with previous five-symbol full-day evidence. Scope UTC [2026-05-19 00:00, 2026-05-20 00:00). Other venues explicitly excluded.

**Meaning of each row:** `HISTORICAL_PILOT_PASS` means journal recorded an executed full-day run for this coin/indicator under the OLD 15/30 group readiness gate. `NEW_CONTRACT_QC_PENDING` means there is no separately verified complete §21.6 per-(instrument, indicator, formula version, scope) feature passport. These are distinct fields, not contradictory statuses. Do not erase previous results or automatically count new READY. Versioned source path is not sufficient evidence of independent per-row QC. No trade thresholds or OOS edge claimed.

| Instrument | # | Indicator | Calculator | Previous day evidence | §21 per-pair contract |
|---|---:|---|---|---|---|
| APTUSDT | 1 | RVOL | `trade_minute_factors_v1.py` | HISTORICAL_PILOT_PASS | NEW_CONTRACT_QC_PENDING |
| APTUSDT | 2 | Trade Rate | `trade_minute_factors_v1.py` | HISTORICAL_PILOT_PASS | NEW_CONTRACT_QC_PENDING |
| APTUSDT | 3 | Trade Rate Acceleration | `trade_minute_factors_v1.py` | HISTORICAL_PILOT_PASS | NEW_CONTRACT_QC_PENDING |
| APTUSDT | 4 | Large Trade Share | `fill_microstructure_minutes_v1.py` | HISTORICAL_PILOT_PASS | NEW_CONTRACT_QC_PENDING |
| APTUSDT | 5 | Buy/Sell Delta | `trade_minute_factors_v1.py` | HISTORICAL_PILOT_PASS | NEW_CONTRACT_QC_PENDING |
| APTUSDT | 6 | CVD | `trade_minute_factors_v1.py` | HISTORICAL_PILOT_PASS | NEW_CONTRACT_QC_PENDING |
| APTUSDT | 7 | Delta Acceleration | `trade_minute_factors_v1.py` | HISTORICAL_PILOT_PASS | NEW_CONTRACT_QC_PENDING |
| APTUSDT | 8 | Trade Streaks | `fill_microstructure_minutes_v1.py` | HISTORICAL_PILOT_PASS | NEW_CONTRACT_QC_PENDING |
| APTUSDT | 9 | Queue Imbalance | `book_minute_three_v1.py` | HISTORICAL_PILOT_PASS | NEW_CONTRACT_QC_PENDING |
| APTUSDT | 11 | Order Book Depth | `book_minute_three_v1.py` | HISTORICAL_PILOT_PASS | NEW_CONTRACT_QC_PENDING |
| APTUSDT | 18 | Short-Term Autocorrelation | `price_minute_features_v1.py` | HISTORICAL_PILOT_PASS | NEW_CONTRACT_QC_PENDING |
| APTUSDT | 20 | Local Extremes | `price_minute_features_v1.py` | HISTORICAL_PILOT_PASS | NEW_CONTRACT_QC_PENDING |
| APTUSDT | 27 | Inter-Trade Duration | `fill_microstructure_minutes_v1.py` | HISTORICAL_PILOT_PASS | NEW_CONTRACT_QC_PENDING |
| APTUSDT | 28 | Trade-Size Distribution | `fill_microstructure_minutes_v1.py` | HISTORICAL_PILOT_PASS | NEW_CONTRACT_QC_PENDING |
| APTUSDT | 29 | Bid/Ask Spread | `book_minute_three_v1.py` | HISTORICAL_PILOT_PASS | NEW_CONTRACT_QC_PENDING |
| INJUSDT | 1 | RVOL | `trade_minute_factors_v1.py` | HISTORICAL_PILOT_PASS | NEW_CONTRACT_QC_PENDING |
| INJUSDT | 2 | Trade Rate | `trade_minute_factors_v1.py` | HISTORICAL_PILOT_PASS | NEW_CONTRACT_QC_PENDING |
| INJUSDT | 3 | Trade Rate Acceleration | `trade_minute_factors_v1.py` | HISTORICAL_PILOT_PASS | NEW_CONTRACT_QC_PENDING |
| INJUSDT | 4 | Large Trade Share | `fill_microstructure_minutes_v1.py` | HISTORICAL_PILOT_PASS | NEW_CONTRACT_QC_PENDING |
| INJUSDT | 5 | Buy/Sell Delta | `trade_minute_factors_v1.py` | HISTORICAL_PILOT_PASS | NEW_CONTRACT_QC_PENDING |
| INJUSDT | 6 | CVD | `trade_minute_factors_v1.py` | HISTORICAL_PILOT_PASS | NEW_CONTRACT_QC_PENDING |
| INJUSDT | 7 | Delta Acceleration | `trade_minute_factors_v1.py` | HISTORICAL_PILOT_PASS | NEW_CONTRACT_QC_PENDING |
| INJUSDT | 8 | Trade Streaks | `fill_microstructure_minutes_v1.py` | HISTORICAL_PILOT_PASS | NEW_CONTRACT_QC_PENDING |
| INJUSDT | 9 | Queue Imbalance | `book_minute_three_v1.py` | HISTORICAL_PILOT_PASS | NEW_CONTRACT_QC_PENDING |
| INJUSDT | 11 | Order Book Depth | `book_minute_three_v1.py` | HISTORICAL_PILOT_PASS | NEW_CONTRACT_QC_PENDING |
| INJUSDT | 18 | Short-Term Autocorrelation | `price_minute_features_v1.py` | HISTORICAL_PILOT_PASS | NEW_CONTRACT_QC_PENDING |
| INJUSDT | 20 | Local Extremes | `price_minute_features_v1.py` | HISTORICAL_PILOT_PASS | NEW_CONTRACT_QC_PENDING |
| INJUSDT | 27 | Inter-Trade Duration | `fill_microstructure_minutes_v1.py` | HISTORICAL_PILOT_PASS | NEW_CONTRACT_QC_PENDING |
| INJUSDT | 28 | Trade-Size Distribution | `fill_microstructure_minutes_v1.py` | HISTORICAL_PILOT_PASS | NEW_CONTRACT_QC_PENDING |
| INJUSDT | 29 | Bid/Ask Spread | `book_minute_three_v1.py` | HISTORICAL_PILOT_PASS | NEW_CONTRACT_QC_PENDING |
| DOTUSDT | 1 | RVOL | `trade_minute_factors_v1.py` | HISTORICAL_PILOT_PASS | NEW_CONTRACT_QC_PENDING |
| DOTUSDT | 2 | Trade Rate | `trade_minute_factors_v1.py` | HISTORICAL_PILOT_PASS | NEW_CONTRACT_QC_PENDING |
| DOTUSDT | 3 | Trade Rate Acceleration | `trade_minute_factors_v1.py` | HISTORICAL_PILOT_PASS | NEW_CONTRACT_QC_PENDING |
| DOTUSDT | 4 | Large Trade Share | `fill_microstructure_minutes_v1.py` | HISTORICAL_PILOT_PASS | NEW_CONTRACT_QC_PENDING |
| DOTUSDT | 5 | Buy/Sell Delta | `trade_minute_factors_v1.py` | HISTORICAL_PILOT_PASS | NEW_CONTRACT_QC_PENDING |
| DOTUSDT | 6 | CVD | `trade_minute_factors_v1.py` | HISTORICAL_PILOT_PASS | NEW_CONTRACT_QC_PENDING |
| DOTUSDT | 7 | Delta Acceleration | `trade_minute_factors_v1.py` | HISTORICAL_PILOT_PASS | NEW_CONTRACT_QC_PENDING |
| DOTUSDT | 8 | Trade Streaks | `fill_microstructure_minutes_v1.py` | HISTORICAL_PILOT_PASS | NEW_CONTRACT_QC_PENDING |
| DOTUSDT | 9 | Queue Imbalance | `book_minute_three_v1.py` | HISTORICAL_PILOT_PASS | NEW_CONTRACT_QC_PENDING |
| DOTUSDT | 11 | Order Book Depth | `book_minute_three_v1.py` | HISTORICAL_PILOT_PASS | NEW_CONTRACT_QC_PENDING |
| DOTUSDT | 18 | Short-Term Autocorrelation | `price_minute_features_v1.py` | HISTORICAL_PILOT_PASS | NEW_CONTRACT_QC_PENDING |
| DOTUSDT | 20 | Local Extremes | `price_minute_features_v1.py` | HISTORICAL_PILOT_PASS | NEW_CONTRACT_QC_PENDING |
| DOTUSDT | 27 | Inter-Trade Duration | `fill_microstructure_minutes_v1.py` | HISTORICAL_PILOT_PASS | NEW_CONTRACT_QC_PENDING |
| DOTUSDT | 28 | Trade-Size Distribution | `fill_microstructure_minutes_v1.py` | HISTORICAL_PILOT_PASS | NEW_CONTRACT_QC_PENDING |
| DOTUSDT | 29 | Bid/Ask Spread | `book_minute_three_v1.py` | HISTORICAL_PILOT_PASS | NEW_CONTRACT_QC_PENDING |
| LTCUSDT | 1 | RVOL | `trade_minute_factors_v1.py` | HISTORICAL_PILOT_PASS | NEW_CONTRACT_QC_PENDING |
| LTCUSDT | 2 | Trade Rate | `trade_minute_factors_v1.py` | HISTORICAL_PILOT_PASS | NEW_CONTRACT_QC_PENDING |
| LTCUSDT | 3 | Trade Rate Acceleration | `trade_minute_factors_v1.py` | HISTORICAL_PILOT_PASS | NEW_CONTRACT_QC_PENDING |
| LTCUSDT | 4 | Large Trade Share | `fill_microstructure_minutes_v1.py` | HISTORICAL_PILOT_PASS | NEW_CONTRACT_QC_PENDING |
| LTCUSDT | 5 | Buy/Sell Delta | `trade_minute_factors_v1.py` | HISTORICAL_PILOT_PASS | NEW_CONTRACT_QC_PENDING |
| LTCUSDT | 6 | CVD | `trade_minute_factors_v1.py` | HISTORICAL_PILOT_PASS | NEW_CONTRACT_QC_PENDING |
| LTCUSDT | 7 | Delta Acceleration | `trade_minute_factors_v1.py` | HISTORICAL_PILOT_PASS | NEW_CONTRACT_QC_PENDING |
| LTCUSDT | 8 | Trade Streaks | `fill_microstructure_minutes_v1.py` | HISTORICAL_PILOT_PASS | NEW_CONTRACT_QC_PENDING |
| LTCUSDT | 9 | Queue Imbalance | `book_minute_three_v1.py` | HISTORICAL_PILOT_PASS | NEW_CONTRACT_QC_PENDING |
| LTCUSDT | 11 | Order Book Depth | `book_minute_three_v1.py` | HISTORICAL_PILOT_PASS | NEW_CONTRACT_QC_PENDING |
| LTCUSDT | 18 | Short-Term Autocorrelation | `price_minute_features_v1.py` | HISTORICAL_PILOT_PASS | NEW_CONTRACT_QC_PENDING |
| LTCUSDT | 20 | Local Extremes | `price_minute_features_v1.py` | HISTORICAL_PILOT_PASS | NEW_CONTRACT_QC_PENDING |
| LTCUSDT | 27 | Inter-Trade Duration | `fill_microstructure_minutes_v1.py` | HISTORICAL_PILOT_PASS | NEW_CONTRACT_QC_PENDING |
| LTCUSDT | 28 | Trade-Size Distribution | `fill_microstructure_minutes_v1.py` | HISTORICAL_PILOT_PASS | NEW_CONTRACT_QC_PENDING |
| LTCUSDT | 29 | Bid/Ask Spread | `book_minute_three_v1.py` | HISTORICAL_PILOT_PASS | NEW_CONTRACT_QC_PENDING |
| ARBUSDT | 1 | RVOL | `trade_minute_factors_v1.py` | HISTORICAL_PILOT_PASS | NEW_CONTRACT_QC_PENDING |
| ARBUSDT | 2 | Trade Rate | `trade_minute_factors_v1.py` | HISTORICAL_PILOT_PASS | NEW_CONTRACT_QC_PENDING |
| ARBUSDT | 3 | Trade Rate Acceleration | `trade_minute_factors_v1.py` | HISTORICAL_PILOT_PASS | NEW_CONTRACT_QC_PENDING |
| ARBUSDT | 4 | Large Trade Share | `fill_microstructure_minutes_v1.py` | HISTORICAL_PILOT_PASS | NEW_CONTRACT_QC_PENDING |
| ARBUSDT | 5 | Buy/Sell Delta | `trade_minute_factors_v1.py` | HISTORICAL_PILOT_PASS | NEW_CONTRACT_QC_PENDING |
| ARBUSDT | 6 | CVD | `trade_minute_factors_v1.py` | HISTORICAL_PILOT_PASS | NEW_CONTRACT_QC_PENDING |
| ARBUSDT | 7 | Delta Acceleration | `trade_minute_factors_v1.py` | HISTORICAL_PILOT_PASS | NEW_CONTRACT_QC_PENDING |
| ARBUSDT | 8 | Trade Streaks | `fill_microstructure_minutes_v1.py` | HISTORICAL_PILOT_PASS | NEW_CONTRACT_QC_PENDING |
| ARBUSDT | 9 | Queue Imbalance | `book_minute_three_v1.py` | HISTORICAL_PILOT_PASS | NEW_CONTRACT_QC_PENDING |
| ARBUSDT | 11 | Order Book Depth | `book_minute_three_v1.py` | HISTORICAL_PILOT_PASS | NEW_CONTRACT_QC_PENDING |
| ARBUSDT | 18 | Short-Term Autocorrelation | `price_minute_features_v1.py` | HISTORICAL_PILOT_PASS | NEW_CONTRACT_QC_PENDING |
| ARBUSDT | 20 | Local Extremes | `price_minute_features_v1.py` | HISTORICAL_PILOT_PASS | NEW_CONTRACT_QC_PENDING |
| ARBUSDT | 27 | Inter-Trade Duration | `fill_microstructure_minutes_v1.py` | HISTORICAL_PILOT_PASS | NEW_CONTRACT_QC_PENDING |
| ARBUSDT | 28 | Trade-Size Distribution | `fill_microstructure_minutes_v1.py` | HISTORICAL_PILOT_PASS | NEW_CONTRACT_QC_PENDING |
| ARBUSDT | 29 | Bid/Ask Spread | `book_minute_three_v1.py` | HISTORICAL_PILOT_PASS | NEW_CONTRACT_QC_PENDING |

| APTUSDT | 19 | Range Compression | `range_vwap_profile_v1.py` + independent QC | FULL_DAY_5_OF_5 + PHYSICAL_QC_PASS | PASSPORT_COMPLETION_PENDING |
| APTUSDT | 22 | VWAP / Volume Profile | `range_vwap_profile_v1.py` + independent QC | FULL_DAY_5_OF_5 + PHYSICAL_QC_PASS | PASSPORT_COMPLETION_PENDING |
| INJUSDT | 19 | Range Compression | `range_vwap_profile_v1.py` + independent QC | FULL_DAY_5_OF_5 + PHYSICAL_QC_PASS | PASSPORT_COMPLETION_PENDING |
| INJUSDT | 22 | VWAP / Volume Profile | `range_vwap_profile_v1.py` + independent QC | FULL_DAY_5_OF_5 + PHYSICAL_QC_PASS | PASSPORT_COMPLETION_PENDING |
| DOTUSDT | 19 | Range Compression | `range_vwap_profile_v1.py` + independent QC | FULL_DAY_5_OF_5 + PHYSICAL_QC_PASS | PASSPORT_COMPLETION_PENDING |
| DOTUSDT | 22 | VWAP / Volume Profile | `range_vwap_profile_v1.py` + independent QC | FULL_DAY_5_OF_5 + PHYSICAL_QC_PASS | PASSPORT_COMPLETION_PENDING |
| LTCUSDT | 19 | Range Compression | `range_vwap_profile_v1.py` + independent QC | FULL_DAY_5_OF_5 + PHYSICAL_QC_PASS | PASSPORT_COMPLETION_PENDING |
| LTCUSDT | 22 | VWAP / Volume Profile | `range_vwap_profile_v1.py` + independent QC | FULL_DAY_5_OF_5 + PHYSICAL_QC_PASS | PASSPORT_COMPLETION_PENDING |
| ARBUSDT | 19 | Range Compression | `range_vwap_profile_v1.py` + independent QC | FULL_DAY_5_OF_5 + PHYSICAL_QC_PASS | PASSPORT_COMPLETION_PENDING |
| ARBUSDT | 22 | VWAP / Volume Profile | `range_vwap_profile_v1.py` + independent QC | FULL_DAY_5_OF_5 + PHYSICAL_QC_PASS | PASSPORT_COMPLETION_PENDING |

## Audit findings and next verification

1. Trade indicators (#1–8, #27–28): precise definition of units, fill vs parent order, 0.1ms event grouping, turnover and per-coin prior-window denominators are required; old journal supplies executed counts and several partial formula definitions but not one complete new §21 passport per pair.
2. Price indicators (#18, #20): explicit minute-close availability, staleness/forward-fill semantics, return units and non-additive aggregation need per-pair registration.
3. Book indicators (#9, #11, #29): ±5bps dynamic-mid convention, bid/ask notional in USDT, exact snapshot/delta age, schema and QC identity need per-pair registration; book reader produces 1440/1440 minute states across five symbols.
4. #19 Range Compression and #22 running VWAP/POC remain OPEN; APT-only smoke has already passed but four other full-day results and independent source-unit QC are missing. Quote-weighted old estimator is distinct from base-volume VWAP, not a failed reconciliation of the same formula.
5. Record source hashes, denominator identity, formula version, output/coverage and manual QC, then promote individual row only on verified §21 gate. Reuse existing executed source evidence where valid; no need to rerun merely to regenerate known numerics. External venues and #26 Cross-Venue Lead–Lag remain deferred last.

**Audit totals:** 75 historical pilot passes, **0 newly attested §21 per-pair passports in this audit**, 75 pending re-audit; this zero is about NEW attestation, not loss of prior work. Remaining 15 indicators still open under the previous 30-candidate checklist.

## 2026-10-19 #19/#22 supplement

Ten new pairs are listed above with full-day and independent physical QC evidence in the journal. Not yet a formal §21.6 per-pair RESEARCH-READY promotion. Distinct first-day quote-notional-weighted legacy VWAP remains historical separate estimator.
