# CRIPTA — рабочий проход 2/5: price/flow №14–22

**CHECKED HERE: 2026-10-21; RESEARCH EVIDENCE, NOT CANON, NOT RESEARCH-READY.** GitHub authoritative `PH1119057/cripta:main` baseline HEAD `ede850ecc157d6b04c471bfc70f5cdf73d57e1cd`. Границы измерения и READY-gates принадлежат `docs/RESEARCH_COMPUTE_RULES_RU.md §21`. Запрещены молчаливый перенос исследования в Strategy/Entry/Exit/LIVE и смешение instrument scopes.

## 1. Scope и завершённые существующие evidence

Bybit public trades, контрольный день 2026-05-20 UTC, exact instruments `APTUSDT/INJUSDT/DOTUSDT/LTCUSDT/ARBUSDT`, девять показателей №14–22 (45 физических пар при однодневном scope). Исторический статус по `INDICATOR_PROGRESS_2026-10-19.md` и ранее зафиксированным git research артефактам:

| ID | Physical historical computation | Confirmed independent scope | Key limitations |
|---|---|---|---|
| 14 Price Impact | 4/5 (ARB upstream BLOCKED) | 4/5 limited pilot/partitions | Real measure: signed flow versus NEXT minute price return = FUTURE LABEL, not marginal single-order impact |
| 15 Effort/Result | 5/5 | Independent summary 5/5; two-path all-minute algebra 5/5 | Intraminute endpoint bps per million USDT volume, not causal order impact; NOT primary persisted row equality |
| 16 Absorption | 5/5 | Independent Decimal aggregate/partition summary 5/5 | Flow opposed by endpoint minute price move = proxy, not proof of passive order absorption |
| 17 Exhaustion | 5/5 | Independent 5m summary/reversal label partition 5/5 | Flow decay observable end current 5m; next 5m is outcome label only |
| 18 Short-Term Autocorrelation | 3/5 (LTC/ARB upstream BLOCKED) | Partial sample | `float(timestamp)`, carried-forward trade closes, zero-filled returns during empty minutes: not autocorrelation of 60 separately traded minutes |
| 19 Range Compression | 5/5 | Source SHA256 plus 1440-minute joined row hash QC 5/5 | Twenty completed minute price closes, historical one-day source; observed/receipt clock not proved |
| 20 Local Extremes | 3/5 (LTC/ARB upstream BLOCKED) | Partial sample | Extremes of **completed trade-close minutes** incl. stale carry; not print/candle high-low; float timestamp |
| 21 Sweep/Reclaim | 5/5 | Independent existing aggregate summary 5/5; NEW distinct-timestamp sensitivity read below | Trade-print extrema, not book liquidity sweep; timestamp-tied ordering not exchange-proved |
| 22 VWAP / Volume Profile | 5/5 | Source SHA256 plus independent joined 1440-minute row hash QC 5/5 | Correct VWAP base-quantity weighted, exact-price base quantity POC; one-day scope, no observed clock |

**Historic pilot computations: 40/45 symbol-indicator cells physically visited**, with heterogeneous QC depth, not 40/45 strict READY. More independent QC and full owner-selected period required. #19/#22 `passports/range_vwap_2026-05-20_per_pair_gate_v2.json` inspected; do not rerun its completed hashes without a demonstrated defect.

## 2. NEW read-only May20 #21 equal-timestamp sensitivity (all five, rc0)

Exact raw input paths are the five May20 `public_trades` compressed source files whose sha256 are registered in `PASS1_TRADE_TAPE_CHECKPOINT_2026-10-20.md`. Executed as `cripta` via already approved read-only SentinelX route; source reads only; no server writes, no jobs/services touched.

**Collision inventory:** consecutive same raw timestamp group, count if group has two or more fills; multi-price/multi-side counts do not assert real exchange within-group execution order.

| Symbol | Tied timestamp groups | Tied fills | Groups with multiple prices | Groups with both sides |
|---|---:|---:|---:|---:|
| APTUSDT | 23737 | 109451 | 2917 | 18 |
| INJUSDT | 49742 | 240940 | 6322 | 88 |
| DOTUSDT | 28461 | 143542 | 3054 | 14 |
| LTCUSDT | 12064 | 85816 | 1280 | 22 |
| ARBUSDT | 40674 | 198104 | 8110 | 53 |

**Sensitivity method:** independent in-memory Decimal minute partition; only minute `i` after prior 60 **nonempty, completed** minute windows. Historical print-based crossing of prior60 extrema computed with the `sweep_reclaim_ordered_trades_v2.py` source-loop semantics, and compared with a *separate conservative interpretation*: within one timestamp group process group min/max simultaneously, then permit a reclaim only if its crossing was detected in a **strictly earlier timestamp group**. This is a bounded alternative estimator, **NOT a silent repair/redefinition of #21**, and it does not establish that CSV row-order is incorrect.

| Symbol | Eligible minutes | High sweeps | Original high reclaim → distinct-ts | Low sweeps | Original low reclaim → distinct-ts |
|---|---:|---:|---|---:|---|
| APTUSDT | 1380 | 55 | 40 → 40 | 60 | 34 → 34 |
| INJUSDT | 1380 | 80 | 68 → 68 | 62 | 47 → 47 |
| DOTUSDT | 1193 | 50 | 31 → 31 | 53 | 31 → 31 |
| LTCUSDT | 872 | 32 | 22 → 22 | 31 | **18 → 17** |
| ARBUSDT | 1380 | 49 | 35 → 35 | 67 | 40 → 40 |

**FINDING:** one LTC May20 low-side reclaim count in this study depends on allowing within-timestamp sequence ordering (18 original vs 17 timestamp-strict). Four other coins show no *aggregate count* change, not proof that individual event identity is perfectly preserved. This does not by itself validate the source ordering, liquidity sweep interpretation, or later research period.

## 3. NEW #17 five-minute source-coverage QC

Independent read-only exact Decimal timestamp minute index, all 288 5m blocks on 2026-05-20 UTC:

| Symbol | Nonempty 5m blocks / 288 | Minimum fills in any 5m block |
|---|---:|---:|
| APTUSDT | 288 | 60 |
| INJUSDT | 288 | 193 |
| DOTUSDT | 288 | 94 |
| LTCUSDT | 288 | 22 |
| ARBUSDT | 288 | 170 |

Source static finding: `flow_decay_5m_v2.py` currently uses `nxt["n"]` in the feature/label shared **sample eligibility** check. Next-block data are not available at current block's end; the population of *features* must not silently require a future block. On this exact May20 file there are no fully empty 5m blocks, so this future nonempty requirement does **not** remove events solely due to empty next blocks. This is **NOT** a general causal QC PASS: forward label availability and current feature admission require separate variants/contract across the full period.

## 4. Decisions and next gate

- **`PASS2_EVIDENCE_REVIEW=COMPLETE_WITH_GATES_OPEN`**, **`RESEARCH_READY=0/45`**; no change to current canon, physical proxy owner or code. Existing first pass READY also remains 0/50.
- Exact `event_time` is present, `observed_at` / source publication / actual as-of receipt clock **not proven**. A computed minute closing time does not prove feed availability.
- Full owner-selected multi-day period and per-symbol run manifests plus complete independent per-row/per-event source-correctness QC remain OPEN, including #15–17/#21 which currently have mostly summary/partition QC.
- Upstream-blocked route for #14 ARB and #18/#20 LTC/ARB **not retried** or recast through a different executor. Partial coverage stays partial, not zeros. Any alternate execution requires distinct approved route.
- Owner decision would be needed if replacing #21 print-extrema proxy with strict book-liquidity sweep; no such decision assumed. #14 future return and #17 next-block returns must remain outcome labels.
- **Proceed next to group 3 / book #9–13/#29–30 evidence review** without redefining two previous groups as `RESEARCH-READY`. Cross-venue #26 remains last. After fifth work package, final inventory shall explicitly distinguish strict READY gaps from completed research measurements.

Exact source identity inspected in GitHub main: `signed_flow_next_minute_v2.py`, `effort_result_minute_v2.py`, `absorption_prior60_candidate_v2.py`, `flow_decay_5m_v2.py`, `sweep_reclaim_ordered_trades_v2.py`, `price_minute_features_v1.py`; also previous `DEEP_GATE_PRICE_BOOK_STATIC_AUDIT_2026-10-20.md` and `PHYSICAL_CONTRACT_GAPS_PASS2_2026-10-20.md`.
