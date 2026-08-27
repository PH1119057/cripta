# Algorithm 2 — Entry Research V4 / P31

P31 continues entry-only research. It does not alter live order execution, stop-loss,
take-profit, leverage, risk gates, or any exit policy.

## Why P31 exists

P30 identified a useful pattern: medium-window taker pressure can remain against the
future trade while the most recent minute turns in the trade direction. P31 tests this
without promoting it to a hard gate.

P31 also removes an important five-minute-candle ambiguity. A P30 candidate exists when
a five-minute candle reaches a known limit price, but OHLC cannot tell whether the candle
made its high before or after the limit was actually touched. P31 replays the retained
Bybit public-trade tape to find the first real trade that reaches the entry price inside
the candidate bar. Exact +0.5/-0.5 and +0.5/-1.0 ordering starts only after that touch.

## Causal flow windows

For every real touch P31 uses only fully completed minutes before the minute containing
the touch:

- `pressure`: four completed minutes before the last completed minute;
- `reversal`: the last completed minute before the touch minute;
- all deltas are direction-adjusted, so positive always means flow in the proposed trade
  direction and negative means pressure against it.

The key research state is `pressure_then_reversal`: pressure < 0 and reversal > 0.

## Outputs

`reports/entry_research_v4/...` contains:

- `signals_touch_exact.csv` — every P30 candidate with raw-tape touch time, exact timing
  outcomes, and touch-aligned flow features;
- `threshold_matrix.csv` — diagnostic pressure/reversal threshold grid (0..50% in 10%
  steps); no cell is automatically selected for live trading;
- `summary.json` — baseline, flow-state controls, Long/Short splits, 30-day slices, and a
  direct comparison of P30 five-minute OHLC ordering against P31 exact tape ordering.

## Methodology rule

P31 is research only. Do not choose a threshold from one aggregate number. A candidate
threshold must remain useful across the three 30-day slices and in both directions before
it can receive any weight in the future 100-point entry score.
