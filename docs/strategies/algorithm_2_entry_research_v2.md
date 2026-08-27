# Algorithm 2 — Entry Research V2 (P29)

P29 changes the research baseline before adding more indicators.

## Architecture under test

- **1h = strategic direction context only.** The 1h support/resistance zone is no longer required to overlap the execution zone.
- **15m + 5m = entry location.** A Long candidate requires 15m and 5m support zones to be close/overlapping and the current 5m bar to touch the known 5m support boundary. Short is symmetric.
- **Causal regime reset after a shock.** A shock is detected only from already-closed candles. After the latest shock, zone history starts at that shock instead of blindly using all 130 bars.
- **One-hour embargo after a shock.** By default no 5m/15m entry zone is considered mature until 60 minutes of new post-shock data exist.
- **Frozen dataset.** 5m/15m/60m candles are downloaded once, fingerprinted and reused by both P28-adaptive baseline and P29 V2 in the same run.
- **Six-hour outcome horizon.** Adds 360m MFE/MAE so a five-hour intraday move is not clipped by the old 240m ceiling.

P29 intentionally does **not** add taker-flow, RSI, MACD, order-book data, or new exit logic. Those are later layers. The goal is to measure whether the corrected timeframe architecture itself improves signal frequency and timing.

## Entry-frequency sensitivity

`CooldownBars` remains explicit. Recommended comparison on the same frozen dataset:

- 12 bars = 60 minutes (conservative, fewer correlated repeat touches)
- 6 bars = 30 minutes
- 3 bars = 15 minutes (closer to the desired intraday signal frequency)

Do not use zero cooldown as a production assumption: it can count repeated touches of the same zone before an earlier hypothetical trade would have been resolved.
