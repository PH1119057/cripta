# Algorithm 2 Entry Research V7 — P34

P34 remains entry research only. It does not change live execution, Stop Loss, Take Profit,
position sizing, or exit logic.

## Strategy risk convention recorded

For this strategy, the working `1%` invalidation concept means **a 1% adverse move from the
entry price**. It is not a statement that 1% of account equity should be risked on every trade.
The existing live Risk Engine still contains an older equity-percentage sizing control and is
left unchanged in P34; that mismatch must be redesigned deliberately when risk management is
revisited.

P33 showed two different statistics that must not be confused:

- about one fifth of eventual +0.5% opportunities crossed -1% before reaching +0.5%;
- across all candidates, the complete +0.5% versus -1% first-hit race has a materially larger
  adverse-first share.

P34 therefore keeps `-1% from entry` as a structural research boundary, not as a claim about
final live stop-out frequency.

## Why Open Interest is the next layer

The frozen P30 dataset already contains 5-minute Open Interest. P34 uses it without any new
market download and asks whether leveraged positioning adds information to the existing
15M/5M zone + taker-flow picture.

For every P33 candidate P34 records causal OI changes over 5, 15, 30, and 60 minutes before the
candidate bar, plus:

- 60-minute OI quartiles;
- short-vs-long OI acceleration;
- expansion / deleveraging states;
- the combination of direction-adjusted price movement and OI expansion/contraction;
- interactions with the already-known taker-flow state;
- three independent 30-day stability slices.

No OI threshold is promoted to a trading rule in P34. The purpose is to learn whether OI earns
future Entry Quality points or should be discarded.

## Invalidation pause baseline

P33 also showed that a causal pause after a `-1% before +0.5%` failure improved the quality of
later candidates. P34 carries the 60-minute version only as a comparison baseline. It does not
create live cooldown logic.
