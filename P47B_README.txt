P47B RUNNER MANAGEMENT V1
========================

Purpose
-------
Research only the part of the position AFTER the frozen prefix:

  Entry -> +0.10% -> raw-price BE -> +1.10% -> locked floor +1.00% -> RUNNER

Entry V1 is frozen. P47B does not change live execution, sizing, leverage,
re-entry, partial TP, or portfolio risk.

Policy families
---------------
1. CONTROL
   Hold the +1.00% floor after +1.10% and mark an unclosed runner at 72h.

2. STEP
   At milestones +1.5/+2/+3/+5/+10%, raise the floor to
   milestone - fixed giveback. Giveback candidates: 0.25/0.50/0.75/1.00%.

3. MFE GIVEBACK
   After +1.10%, floor = max(+1.00%, running MFE - giveback).
   Giveback candidates: 0.25/0.50/0.75/1.00/1.50%.

4. STRUCTURAL
   Causal swing protection. A pullback low can become a stop reference only
   after price rebounds enough to confirm the low. No future swing is used.

Default structural presets:
  pullback 0.25 / rebound 0.25 / buffer 0.05%
  pullback 0.50 / rebound 0.25 / buffer 0.05%
  pullback 0.50 / rebound 0.50 / buffer 0.05%
  pullback 0.75 / rebound 0.25 / buffer 0.10%
  pullback 0.75 / rebound 0.50 / buffer 0.10%

Run
---
From C:\cripta:

  powershell -ExecutionPolicy Bypass -File .\scripts\check_windows.ps1

Then:

  powershell -ExecutionPolicy Bypass -File .\scripts\research_runner_management_uni_link_windows.ps1

Outputs
-------
  reports\runner_management_v1\UNI_LINK_<timestamp>\summary.md
  reports\runner_management_v1\UNI_LINK_<timestamp>\summary.json
  reports\runner_management_v1\UNI_LINK_<timestamp>\policy_summary.csv
  reports\runner_management_v1\UNI_LINK_<timestamp>\policy_results.csv

Important interpretation
------------------------
Gross percentages are price-move research on equal notional. Fees, slippage,
funding, position sizing and leverage are NOT deducted in this module.

The default early floor is raw price BE (0.00%). This preserves the exact
P47A/P46 research prefix. Economic BE with real Bybit costs is a later live-cost
translation, not a reason to re-tune the research sample here.

The `fixed_notional_30d_equivalent_pct` field is a normalization of the sum of
trade price returns over the calendar span. It is NOT an account equity return
promise, because trades can overlap and sizing is not yet modeled.
