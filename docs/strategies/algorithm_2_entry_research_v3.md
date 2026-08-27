# Algorithm 2 — Entry Research V3 (P30)

P30 is a research-only pass. It does not change live execution or exit management.

## Questions

1. Does the 15m+5m local confluence remain useful over a 90-day sample?
2. What happens when 1h direction is context only rather than a hard filter?
3. Does a post-shock regime work better when the shock candle itself is excluded from new zones?
4. How stable are entry outcomes across three consecutive 30-day slices?
5. Does real taker Buy/Sell flow correlate with better timing?

## Frozen dataset

The first run finds the latest complete daily public-trade tape, then aligns all data to that completed UTC day.
It stores:

- 5m, 15m, 60m Bybit klines;
- raw daily Bybit public trade `.csv.gz` archives;
- aggregated 1m taker-flow buckets;
- 5m Open Interest (downloaded for future layers, not used as a filter in P30);
- SHA-256/fingerprints in `dataset_manifest.json`.

Raw trade tapes are deliberately retained so future flow definitions can be recomputed without downloading the market again.

## Entry candidate V3

- 15m and 5m support/resistance zones determine location.
- A 1h context is recorded as Long/Short/Neutral and aligned/opposed, but never forbids the opposite local candidate.
- A shock resets the local sample. The shock candle and all older candles are excluded from the new zone.
- New candidates are embargoed for 60 minutes after a shock by default.
- Cooldown is exact elapsed time. Default 30 minutes.

## Taker flow

For each candidate, P30 records the completed public trades strictly before the candidate 5m bar for 1m/5m/15m/30m windows:

- Buy notional;
- Sell notional;
- Delta;
- Delta %;
- directional Delta % (positive means flow confirms the candidate direction);
- trade counts.

Flow does not filter candidates in P30. The report only groups outcomes by flow strength so its value can be tested independently.

## Stability

The 90-day evaluation is split into three consecutive 30-day blocks. The report includes each block and ranges for major rates. A small range is encouraging, but there is no hard requirement that market-regime results differ by only 1–2 percentage points.
