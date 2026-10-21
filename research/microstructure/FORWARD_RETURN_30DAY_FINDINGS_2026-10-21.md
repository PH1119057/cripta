# CRIPTA — historical forward-return screen: findings, not trading signals

**RESEARCH RESULT 2026-10-21. NOT CANON, NOT FULL CAUSAL OOS, NOT RESEARCH-READY, NOT AFTER-FEES P&L.** Frozen exact script `trade_minute_forward_label_30day_screen_v1.py` Git blob `8c784408705775fdb76d648098d9e9be9df092c8`; source protocol `FORWARD_LABEL_30DAY_EXPLORATORY_PROTOCOL_2026-10-21.md`. Full source/QC: `FORWARD_RETURN_5COINS_30DAY_SCREEN_CONSOLIDATED_2026-10-21.json` and the five per-symbol results.

## Executed/QC

- Five independently completed server jobs; 30 source archive days each, exactly 150 raw compressed SHA matches, 37,162,098 trade fills, independent previously verified 6-factor physical minute cube with 1,296,000 rows, and 90 instrument×factor×horizon analyses.
- Exploratory historical labels: price difference in bps from **actual end-minute trade-close** to actual trade-close at t+5/15/60 minutes; no fictitious carry into missing print minutes; within-fold purging of future targets. TRAIN May18–Jun06 / VALIDATION Jun07–09 / late internal holdout Jun10–16 UTC. Physical value q20/q80 thresholds fitted on TRAIN only. No exchange receipt/availability clock, so actual timely usability **not verified**.
- 81/90 associations define both high and low comparison groups in all folds. 9/90 are `UNDEFINED`, principally CVD distribution shifts. 18/90 have same sign of **high-minus-low mean future price return** across all three folds. Six of those 18 are UTC-reset CVD #6 and suffer sparse day support (typically just 1 late-holdout day with both groups). Without CVD, 12/90 remain with descriptively stable sign: APT 0, INJ 4, DOT 1, LTC 2, ARB 5. Ten of those 12 have larger absolute late mean association than the prior-day same-minute feature placebo, but this is **not** hypothesis significance, statistical error control, causality or net edge.
- Number of hypotheses: **90**, and forward minute labels overlap heavily; treating tens of thousands of minutes as independent trials would be pseudoreplication. Only seven internal late-holdout days (six for prior-day feature placebo).

| Coin | Same-sign cells all horizons excluding CVD /18 | Important interpretative caution |
|---|---:|---|
| APTUSDT | 0 | All three original same-sign cells were CVD, highly sparse late low group |
| INJUSDT | 4 | Mostly #3/#7 signed flow acceleration with small bps magnitude |
| DOTUSDT | 1 | #5 Delta at 60min: month-end mean negative, only 3/7 daily effects negative |
| LTCUSDT | 2 | Small conditional bps and cross-month instability |
| ARBUSDT | 5 | #1/#2/#3/#5 proxies show some matching signs, but 7 holdout days too short |

## Illustrative **descriptive** contrasts (NOT strategy P&L)

All values are high-factor mean future return **minus** low-factor mean future return, in **bps of the same symbol's price**, not trade fill return. High/low partitions use each coin's TRAIN-only q20/q80.

| Symbol, factor, horizon | TRAIN (20d) | VALIDATION (3d) | Late holdout (7d) | Prior-day-feature placebo (last 6d) | Holdout days with expected positive/negative contrast |
|---|---:|---:|---:|---:|---|
| ARBUSDT #2 trade rate 5m | +1.030 | +1.822 | +1.040 | −0.278 | positive 5/7 |
| ARBUSDT #2 trade rate 15m | +2.716 | +2.402 | +4.916 | −3.729 | positive 4/7 |
| INJUSDT #7 delta acceleration 15m | −0.912 | −2.361 | −1.189 | +0.413 | negative 5/7 |
| DOTUSDT #5 delta 60m | −1.044 | −12.172 | −2.743 | −0.529 | negative only 3/7 |

**CRITICAL artifact:** APT #6 CVD 5m had a late high-minus-low value −50.5 bps, but the lower group contained **only THREE eligible minute observations** in the entire late holdout; this is invalid evidence for a stable predictive response. APT #6 all three horizons and ARB #6 (only one day with high/low groups in late holdout) require explicit low-sample warning. `None` and missing buckets are not zero.

A 15m ARB trade-rate high-low difference +4.916 bps is **NOT** an entry P&L: no purchase/sale execution, maker/taker routing, spread, impact, slippage, protection, stop, capital occupancy or commissions has been modeled. The holdout linear `r` for this pair was ~−0.0117, so do not misrepresent the high-low contrast as a monotonic predictive law.

## Result classification and follow-on

**FINDING:** some descriptive patterns, no robust proven tradable edge across the five symbols. No indicator is promoted to `RESEARCH-READY`; there is no trade/backtest/live performance claim and no new Strategy policy.

**Next independent historical cohort inventory, checked read-only under `cripta`:** June17–July16 2026 (30 UTC days) has 30/30 named readable raw Bybit compressed trade archives on each of the five coins (150/150 filenames, no missing); bytes are **not yet SHA-manifested or independently event-QC'd in this follow-on**. This is a candidate **fresh later validation period**. Future test must freeze old thresholds/labels/variants before looking at later outcomes, retest *all predeclared hypotheses*, independently register all raw SHA and exact causality gates. OI, liquidations, orderbook and other indicators still absent from this six-factor signal experiment. Cross-venue #26 deferred last.

No server filesystem writes were performed by the **forward-return** screening code; all results were sent to stdout and recorded in GitHub. No LIVE, Strategy, PostgreSQL or service mutation.
