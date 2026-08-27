# Algorithm 2 Entry Research V6 — P33

P33 stays entirely on entry research. It does not change live execution or exit logic.

The question is no longer whether a valid zone must reverse at the exact touch. Small adverse
movement is expected. P33 measures the adverse path after the real trade-tape touch and asks:

- how far does price normally move against a candidate before +0.5% or +1.0% is reached;
- how often 0.1/0.2/0.3/0.4/0.5/0.7/1.0% adverse thresholds occur first;
- whether candidates that hit -1% before +0.5% coincide with the causal P30 shock candle;
- what a 60- or 90-minute pause after such a failure would do to later candidates.

A 1% price move and the 1% account risk budget are separate concepts. P33 studies only the
former as a possible structural invalidation level.
