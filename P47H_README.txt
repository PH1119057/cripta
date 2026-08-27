P47H — TRAILING STOP LADDER EXPLORATION

Purpose
-------
Explore the user's proposed monotonic trailing-stop ladder on the existing frozen
nine-asset Entry V1 sample (1063 signals).

Frozen interpretation
---------------------
Before activation: initial hard stop = -1.00%.
+0.10% MFE -> stop = 0.00%.
+0.20% -> +0.10%; +0.30% -> +0.20%; ... +1.00% -> +0.90%.
Above +1.00%, compare staircase spacing 0.20%, 0.25%, and 0.30%.
The stop never loosens.

Controls
--------
A_SIMPLE_TAKE_1P00
B_FULL_RUNNER_MFE_GB1P50

Methodology
-----------
This is exploratory parameter selection on already-observed assets. The winner
must NOT be called OOS-validated. Freeze the candidate after this run and use
the new five assets or a future untouched time window as the clean validation.

Run
---
powershell -ExecutionPolicy Bypass -File .\scripts\check_windows.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\research_trailing_ladder_all9_windows.ps1

Output
------
C:\cripta\reports\trailing_ladder_v1\ALL9_<timestamp>\
  summary.md
  summary.json
  policy_summary.csv
  policy_results.csv
