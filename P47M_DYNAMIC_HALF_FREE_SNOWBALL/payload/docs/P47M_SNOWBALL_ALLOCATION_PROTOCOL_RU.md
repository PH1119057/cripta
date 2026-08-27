# P47M - dynamic 50% of free deposit snowball replay

Research-only portfolio/capacity experiment. Frozen Entry V1 and P46 are unchanged.

## Rule under test

- Start wallet: 100 USD.
- Leverage: 10x.
- At each complete Entry signal, first release every position whose modeled Exit timestamp is
  already reached.
- Free deposit = current wallet balance minus margin reserved by still-open virtual positions.
- New signal allocation budget = 50% of free deposit.
- Entry fee reserve is included inside that budget. With untouched 100 USD and overlapping
  signals this gives approximately 50 -> 25 -> 12.5 -> 6.25 -> 3.125 USD.
- There is no fixed maximum number of open virtual positions.
- Same-symbol overlaps are intentionally allowed in this research to measure pure capital
  slicing. This is not automatically executable on a Bybit one-way account.
- When any position exits, its reserved margin becomes free immediately. The next signal gets
  50% of the then-current free deposit; the sequence does not continue with a fixed slot number.
- Realized PnL and fees change the wallet and therefore future allocation budgets.

## Exit benchmark

P47K checkpoint contract only:

- success: +1.10%, maker exit;
- original/early stop: -1.00%, taker exit;
- after +0.50 activation: -0.50%, taker exit;
- maker entry.

No +2/+3/+5 runner value is credited.

## Policies

- `HALF_FREE_NO_MIN`: pure mathematical snowball, no artificial minimum allocation.
- `HALF_FREE_MIN6`: same logic but a proposed new allocation below 6 USD is skipped.

The 6 USD value is only a practical sensitivity requested for this research. It is not asserted
as the Bybit exchange minimum for every symbol. Production must use fresh exchange instrument
rules.

## Interpretation

This experiment intentionally differs from P47L. P47L modeled an executable 3-slot portfolio
with one active position per symbol. P47M answers a different question: how many signals and how
much capital can be represented by repeatedly taking half of currently free deposit.

Signal-level same-symbol overlaps are virtual. Before production, exchange position mode,
minimum order rules, chronology, actual fills, funding, slippage and full Exit/Risk must be
validated separately.
