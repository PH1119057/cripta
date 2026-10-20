# CRIPTA — deep gate, price and orderbook (2026-10-20)

**Status: RESEARCH FINDING / NOT CANON / NOT READY.** Source of truth `PH1119057/cripta:main` checked at `50b495c5f11ff073627dccee80789e51e1eb9691`. Read existing `INDICATOR_PROGRESS_2026-10-19.md`, `price_minute_features_v1.py`, `book_minute_three_v1.py`, `effort_result_minute_allrow_qc_v1.py`. **Static code audit**, not fresh server replay or fresh QC numbers.

## Concrete implementation findings

1. **#18/#20 price features — source timestamp precision:** `price_minute_features_v1.py` computes `k=int((float(timestamp)-UTC_start)//60)`. Contrast proven v1→v2 timestamp-boundary fix in minute trade factors using Decimal floor. Risk: boundary assignment for submillisecond or floating-point-represented timestamps. For #18 and #20 no independent exact Decimal membership QC documented. Existing May20 APT/INJ/DOT 3/5 remains valid **as previously reported**, but not proof of complete causal precision; LTC/ARB blocked route: no retry.
2. **#18 autocorrelation:** last close is carried forward when minute has no trades; `r=log(last/previous_last)` can yield 0 on empty minutes. Autocorr window requires 60 non-null returns, **not** 60 minutes with real executions. Thus estimator measures one-minute returns of a *forward-filled trade-close series*, not returns across 60 independently traded minutes. Need variant identity, no silent claim of observed-minute autocorrelation.
3. **#20 local extremes:** `prev=cl[max(0,i-60):i]` computes prior 60 *calendar minute positions* from forward-filled close series. If no actual fills, latest close may be stale and still be compared. The high/low is **of completed minute close samples**, not tick-level extremes or candle intraminute highs/lows. Thus term “Local Extremes” cannot be interpreted as real traded high/low of all prints. Its documented interpretation needs explicit price convention.
4. **#9/#11/#29 book:** `book_minute_three_v1.py` has a **hard-coded** May19 `START=1779148800000` and filename `2026-05-19_..._ob200.data.zip`. This implementation is May19-specific; no May20 portability is proven from this code. Values are latest observed book state within the minute, not an average throughout the minute. The quantity `age_ms` is the time between last book message and minute close. Source-clock receipt latency unknown. Imbalance is `(bid_5bp_usdt-ask_5bp_usdt)/(sum)` and spread is explicit `(ask-bid)/mid*10000` **bps**.
5. **#15 effort/result:** independent `effort_result_minute_allrow_qc_v1.py` reconstructs 1440 buckets and checks algebraically equal minute formulas with Decimal. This is **not** independent direct row equality to a persisted canonical primary cube; prior 5/5 zero mismatches must retain this narrower evidence scope.

## Strict §21.6 processing queue

| Branch | Indicators | Evidence | Next actual gate | State |
|---|---|---|---|---|
| Price with direct numerical proxy | #15/#16/#17/#21 | May20 5/5 independent summary or internal full-minute algebra, varying scope | Exact per-row/effective source timing, causal estimator labels, raw source manifests | OPEN |
| Price partial | #14/#18/#20 | May20 #14 4/5; #18/#20 3/5; unsupported routes blocked | Reconcile blocked route with explicit owner approval for replacement; no covert retry | HARD_STOP on route |
| Book minute stock | #9/#11/#29 | May19 5/5 summary + 1440 minute states | Independent all-row equality, byte provenance, reader port/date semantics, as-of clock | OPEN, old APT job outcome UNKNOWN |
| Book ambiguous mechanism | #10/#12/#13/#30 | Proxy evidence at 5/5 or subset | Physical identity and event/censor timing decisions, independent samples | OWNER_DECISION_REQUIRED where indicated; no silent redefinition |
| Missing source | #23/#24/#25 | Source-gap inventory only | Provider/source provenance + complete coverage | NO_DATA/UNVERIFIED |
| Other venues | #26 | Deferred by owner | Last, after Bybit scope | OWNER_DEFERRED_LAST |

## Gates

No indicator is granted `RESEARCH-READY` based solely on source inspection. Per-pair provenance/manifest, correct observed/available-after timing, full requested period and independent complete QC remain required. Prior no-response/timeout jobs do not get success assigned retroactively. No claim of predictive value or after-fee edge. No code edits or server jobs; no DB, Strategy, deploy or LIVE changes.
