# Algorithm 2 entry research V5 — P32

P32 remains research-only. It does not change live order execution, exits, stops, take-profit, or risk gates.

The objective is to decompose the exhaustion process around the exact P31 touch:

- pressure before the zone;
- whether adverse taker flow decays into the touch;
- whether the pre-touch reversal holds or fails;
- whether pressure continues through touch and reverses only after touch;
- whether large adverse notional produces little additional price progress (absorption proxy).

Post-touch features are diagnostic future information. They are not yet an executable entry rule. A later pass must simulate the actual delayed entry price after any selected confirmation.
