# CRIPTA — рабочий проход 3/5: Bybit orderbook №9–13 и №29–30

**CHECKED HERE: 2026-10-21. RESEARCH RESULT / NOT CANON / NOT RESEARCH-READY.** Source of truth: `PH1119057/cripta:main`; GitHub pre-publication HEAD `cc52f0c2faa6fa342e6f40c64896971da63329d4`. Research §21.4–21.7 и actor §19.5 — обязательны; сжатые ZIP и экспериментальные book readers не доказывают actual observed_at/publication time.

## 1. Cohort и существующий исторический measurement checkpoint

Exact Bybit instrument cohort: APTUSDT/INJUSDT/DOTUSDT/LTCUSDT/ARBUSDT. Indicator set #9 Queue Imbalance, #10 Multi-Level OFI, #11 Order Book Depth, #12 Pulling Rate, #13 Depth Recovery, #29 Bid-Ask Spread, #30 Replenishment/Resiliency. Historical evidence drawn from `INDICATOR_PROGRESS_2026-10-19.md`, `PHYSICAL_CONTRACT_GAPS_PASS2_2026-10-20.md`, `DEEP_GATE_PRICE_BOOK_STATIC_AUDIT_2026-10-20.md`. Historical pilot scopes are **not** all the same UTC day and not §21 READY.

| # | Historical physical evidence | Strict contract/QC limitation |
|---|---|---|
| 9 Queue Imbalance | May19 full-day minute states 5/5, independent rounded summary 5/5 | independent entire 1440-row exact equality not returned; APT prior long job `job_cb166e3a8aa1` no response / outcome UNKNOWN; do not blindly rerun |
| 10 Multi-Level OFI | top1 physical 5/5 + top5 rank stock/decomposition 5/5 in pilot | rank-indexed stock is not price-key-identity flow; pending exact strict formula/variant OWNER DECISION |
| 11 Depth | May19 full-day minute states 5/5, independent rounded summary 5/5 | 5bp quote depth at minute last observed book state, not time average; entire row QC OPEN |
| 12 Pulling | May20 clock/proxy sample 5/5 + event inventory | observed exact-price depletion does NOT distinguish cancellation from execution; trade/book common-clock attribution absent |
| 13 Depth Recovery | May20 day2 5/5 episode inventory with independent raw endpoint age QC | 705 open at day end, two >60s (LTC bid/ARB bid); timer vs right censor semantics and source continuity require OWNER DECISION |
| 29 Spread | May19 full-day minute states 5/5, independent rounded summary 5/5 | explicit (ask-bid)/mid × 10000 bps from latest book state; full-row/clock gate OPEN |
| 30 Replenishment | May20 APT/DOT/LTC 3/5 | INJ `job_e2201594f884` timed out; ARB `job_22c8f6bba901` agent no response; host outcome UNKNOWN, no automatic rerun; top5 stock-recovery proxy != same-price/order resiliency |

**33/35** distinct symbol-indicator cells had historical partial/physical pilot evidence across their **own** declared day/scope; **0/35 strict READY**. Count 33 is an inventory number, not cross-day synchronized feature coverage or full independent QC.

## 2. NEW read-only provenance audit: complete manifest of 10 on-host ZIP files

Command ran as `cripta`, using stdlib `Path/zipfile` central-directory inspection (rc0) and separate read-only streaming `hashlib.sha256` for each full compressed ZIP (rc0). Each archive had **exactly one** named `YYYY-MM-DD_<symbol>_ob200.data` member. Existence and SHA256 verified for both May19 and May20; **ZIP member decompression/CRC, message-level sequence/gap coverage, recorded receiver time, or external immutable origin checksum were NOT independently proved by these two operations**.

| Symbol | UTC day | ZIP compressed bytes | Uncompressed member metadata bytes | Current ZIP SHA256 |
|---|---|---:|---:|---|
| APTUSDT | May19 | 29180688 | 170503384 | `c5481f58fffb5a52a630cc1fcde7f7e954ddabed320461f5abd99accf5478a9c` |
| APTUSDT | May20 | 28502126 | 167041162 | `a82708d204b4faae3361de47c1235d33f526aa0f3243a55b263e2aee6fadcbbd` |
| INJUSDT | May19 | 38618113 | 227612573 | `e9a9407ba675026b409119bea5a362a894f1e7e7c08ddffc438cedc388f79df4` |
| INJUSDT | May20 | 33589356 | 205074363 | `823e529f9ccfd1c214abbffe06fae3ccb9210dbdb95b5735fe3cd9b9a73a7bbb` |
| DOTUSDT | May19 | 31517938 | 187481861 | `c0d40ce1792e6f695a7623b9bffb5188f25b63751bc193eeca780d5424853d84` |
| DOTUSDT | May20 | 31028280 | 185104689 | `5e70ba0ce704802bc6587f3f0b3d1a092b8c2c7553eeac49cffdf94708eb10d6` |
| LTCUSDT | May19 | 17162681 | 121423163 | `b7420afc3ca75c5b9d1377954059781c701e08364345a7731b9171fb8b6f7d7d` |
| LTCUSDT | May20 | 18266516 | 126622110 | `73ba2ca47918aeed02c35f1800b04aa0f882ca8c726560e522fe5613b97bca97` |
| ARBUSDT | May19 | 30970385 | 186814462 | `bc5150520e29cddd9db5f03fe862a590a57e49db3307c0813f8746e07d3ac428` |
| ARBUSDT | May20 | 33243781 | 197142154 | `6506bc21983b71175b83da5fde402ada7c977f5c69dd41d53ce8654ce24a6e41` |

## 3. Inspected reader identity and definitions

GitHub current source inspections (no code mutation):
- `research/microstructure/experiments/book_minute_three_v1.py` (**May19 hard-coded** `START=1779148800000`, input file May19) computes last observed minute-end state using float price/qty, 5bp depth quote USDT, imbalance, spread bps. Cannot relabel this May19 reader as May20 without different source version.
- `price_key_book_flow_v1.py`: Decimal price-key intersection before/after top5, excludes entered/lost rank membership. Visible quantity change does not prove cancellation or fills by actor. Not identical to full strict multi-level OFI.
- `same_price_depletion_v1.py`: prior positive displayed level shrinkage/increase, not cancellations. No trade-join equivalence.
- `exact_price_recovery_v2.py`: per-price pending episode and snapshot reset; `expired` cleanup only triggered by next processed delta, so the observed 2 day-end stale episodes cannot be promoted as valid 60sec live state without temporal contract.
- `book_minute_full_row_independent_qc_v1.py`: independent Decimal 1440 row calculation BUT compares to a GitHub pinned primary source downloaded during execution; old APT server run outcome UNKNOWN. Reader existence ≠ all-coin full-row success.

## 4. Formal classification and next

**PASS3_EVIDENCE_REVIEW=COMPLETE_WITH_GATES_OPEN**. **RESEARCH_READY=0/35** under §21.6. Shared open: immutable ingestion/publication/arrival clock, exact source window and full requested historical period, book snapshot/delta continuity, independent full-row tests, per-pair manifest, unit/variant designation. Three mechanism decisions (#10, #13, #30) must be explicit owner choices if strict terms/formulas are requested. Other proxy measurements continue independently.

No job started/restarted, no host filesystem mutation, no privileges or security policy changed, no DB/Strategy/Execution/LIVE. Only GitHub evidence artifact created. Next work package in owner five-pass plan: group 4 source gaps #23–26; #26 cross-venue only LAST after Bybit research priority and no source fabrication. Fifth work package final summary/QC/OOS scope; do not claim OOS after-fee evidence until actual corresponding simulations are performed.
