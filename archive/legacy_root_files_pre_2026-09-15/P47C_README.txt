P47C CORE + RUNNER SPLIT V1
===========================

Purpose
-------
Test whether partial profit realization at +1.10% can protect the base trade
while giving a smaller runner enough room to survive the UNI/LINK noise seen in
P47B.

Frozen prefix
-------------
  Initial stop -1.00%
  +0.10% -> raw-price BE (0.00%)
  +1.10% -> split decision

The module does NOT tune Entry V1 or the early prefix.

Core accounting
---------------
At +1.10%, the core is conservatively valued at +1.00%, deliberately leaving a
0.10% execution cushion. This is not an assumption that the exchange fill is
exactly +1.00%; it is a conservative research accounting convention.

Default split candidates
------------------------
  100 / 0   control: take +1.00%, no runner
   80 / 20
   75 / 25
   50 / 50

Each partial split is tested with two runner-floor semantics:

  BE / NO-LOOSEN
    Runner stop may not loosen below Entry (0.00%).

  FUNDED -1%
    After core profit is realized, the small runner may use the original -1.00%
    floor. The already realized core finances this extra room.

With conservative core +1.00% accounting:
  80/20 + runner -1.00% => whole episode floor +0.60%
  75/25 + runner -1.00% => whole episode floor +0.50%
  50/50 + runner -1.00% => whole episode floor  0.00%

So no default FUNDED split can make the overall trade episode negative before
execution costs, even though the runner itself is allowed to cross below Entry.

Runner policies
---------------
For each split/floor combination:
  HOLD
  MFE giveback 1.50 / 2.00 / 2.50 / 3.00 / 4.00 / 5.00%

The wider grid is intentional: P47B's best MFE giveback (1.50%) was at the
widest boundary tested, while its +1.00% full-position floor stopped 24/27
runners very quickly.

Run
---
From C:\cripta after extracting this patch:

  powershell -ExecutionPolicy Bypass -File .\scripts\check_windows.ps1

Then:

  powershell -ExecutionPolicy Bypass -File .\scripts\research_core_runner_split_uni_link_windows.ps1

Outputs
-------
  reports\core_runner_split_v1\UNI_LINK_<timestamp>\summary.md
  reports\core_runner_split_v1\UNI_LINK_<timestamp>\summary.json
  reports\core_runner_split_v1\UNI_LINK_<timestamp>\policy_summary.csv
  reports\core_runner_split_v1\UNI_LINK_<timestamp>\policy_results.csv

Quality gate
------------
Default UNI/LINK run must reproduce the frozen prefix:
  signals             227
  initial -1% stops    16
  +0.10% activated    211
  +1.10% split gate    27
  early BE exits      184
  core-only takes      27

summary.json -> prefix_reference_check.all_match must be true.

Interpretation
--------------
Gross values are equal-notional directional price-return research. Fees,
slippage, funding, sizing, leverage, overlapping positions and portfolio risk
are not modeled here. The 30-day value is only a calendar normalization of
signal returns, not an account-equity return promise.
