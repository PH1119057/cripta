# №10 OFI — proposed exact-price-identity measurement contract v0

**Status:** RESEARCH DESIGN / NOT CANON / NOT IMPLEMENTED / NOT READY.
**Authority:** `docs/RESEARCH_COMPUTE_RULES_RU.md §21` at GitHub `PH1119057/cripta:main`.
**Scope:** Bybit ob200, instrument-specific, first historical control day 2026-05-19 UTC; candidate separate formula version, not silent replacement of top1 or rank-based estimator.

## Physical primitives and deterministic reconstruction

For each accepted snapshot / delta with valid exchange event timestamp, reconstruct bid and ask price→quantity books. Maintain the **exact Decimal price key** of each resting level across events and exclude snapshot resets from transitions. An update with a zero quantity removes its price key. Keep raw base quantity and explicit quote-notional `price × quantity` distinct.

For each transition, let `Q_old(side,p)` and `Q_new(side,p)` be visible base-quantity at an **identical exact price p**. For a stable same-price level, `ΔQ(side,p)=Q_new−Q_old`. Record price-key additions/removals and transitions inside vs outside selected top-K membership **separately**; never silently reclassify membership migration as a new/cancel order. A price leaving top K but present deeper is NOT necessarily withdrawn. Whole-book price-key disappearance is visible size loss, still not proven cancellation.

### Proposed output features (not owner-approved canonical OFI)

- `exact_price_delta_bid_base`, `exact_price_delta_ask_base`: components by exact price key within a defined comparison set; no automatic sign/weighted sum.
- `membership_entry_quote` and `membership_exit_quote`: top-K rank boundary movement, separate from same-price deltas.
- `top1_standard_price_transition_ofi_base`: **existing historical estimator** from exact best-price algebra, separate version `top1_price_transition_ofi_v1`.
- `strict_multilevel_ofi`: **UNDEFINED** until owner agrees whether to use levels by absolute price, price-relative distance, initial rank cohort, quote-notional weights and how to handle newly appeared/disappeared prices. Do not relabel sum of rank1..K as strict OFI.

## QC / availability gates

1. Source SHA256, immutable code commit, exact instrument, epoch UTC boundaries; QC snapshot counts and delta monotonicity; detect gaps in seq/u and snapshot restart behavior.
2. Independent reader reconstructs identical per-price snapshots for sampled events and deterministic event/minute hashes; no float price rounding.
3. As-of: event time, capture/observed and feature-available-after must remain separate. Without observed timestamp, `OBSERVED_LATENCY=NOT_VERIFIED`; no feature may claim pre-event availability.
4. Independently verify per-symbol/unit/scope and missingness, compare best-price estimator with historical version only on **same eligible event set**; no mixing Decimal and float outputs as bitwise equivalents.
5. For any feature label / predictive test, future price response is a separate future label. Never use post-event trades in an as-of signal.
6. Register per instrument × formula_version × scope passport §21.6 and full-period count/QC. No threshold, OOS profitability or trading-policy promotion from physical measurement.

**Owner decision required before claiming a canonical new indicator formula.** This design does not alter Strategy, runtime, LIVE or exchange data.
