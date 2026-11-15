P47F — FROZEN EXIT ARCHITECTURE OOS COMPARISON

Purpose
-------
Compare exactly three pre-registered Exit architectures on the seven-asset holdout,
without tuning any new parameter:

A SIMPLE
  -1.00% initial stop
  +0.10% -> BE
  +1.10% reached -> model 100% exit at +1.00%

B FULL RUNNER (P47B frozen winner)
  -1.00% initial stop
  +0.10% -> BE
  +1.10% -> full-position floor +1.00%
  MFE giveback = 1.50%

C SPLIT (P47C/P47E frozen candidate)
  -1.00% initial stop
  +0.10% -> BE
  +1.10% -> 50% core at +1.00%
  remaining 50% runner with BE floor
  MFE giveback = 4.00%

Holdout
-------
BTCUSDT, ETHUSDT, XRPUSDT, 1000PEPEUSDT, SOLUSDT, DOGEUSDT, ADAUSDT
Frozen period tag: 20260518_20260816
Expected frozen core Entry count: 836.

The module builds each exact 72h raw-trade path only once, then evaluates A/B/C on that
same path. It performs a fail-closed source-count check. If a prior P47E HOLDOUT7 report
exists, policy C must reproduce its pooled gross result within 1e-6 or the run fails.

Run
---
1) powershell -ExecutionPolicy Bypass -File .\scripts\check_windows.ps1
2) powershell -ExecutionPolicy Bypass -File .\scripts\research_exit_architecture_oos_full_panel_windows.ps1

Outputs
-------
reports\exit_architecture_oos_v1\HOLDOUT7_<timestamp>\
  summary.md
  summary.json
  architecture_comparison.csv
  policy_summary.csv
  policy_results.csv

Interpretation guardrail
------------------------
This holdout can compare the three architectures fixed before the run. Do NOT use the same
holdout to tune 1.50% or 4.00% giveback values. A new parameter search needs a new forward
sample.
