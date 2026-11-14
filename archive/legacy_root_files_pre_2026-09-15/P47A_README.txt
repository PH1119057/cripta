P47A RETEST ANATOMY — INSTALL / RUN
===================================

Purpose
-------
P47A is a descriptive Exit/Risk research module. Entry V1 remains frozen.
It does NOT execute or tune re-entry. It uses the corrected P46 path-coverage logic
and studies the anatomy of a +1R -> BE reference policy on the UNI/LINK development
sample.

Files added
-----------
src/bybit_workbench/research/retest_anatomy_v14.py
scripts/research_retest_anatomy_uni_link_windows.ps1
tests/test_retest_anatomy_v14.py

Install
-------
Extract this ZIP into C:\cripta preserving directories. No existing source file is
replaced by this patch.

Quality gate
------------
powershell -ExecutionPolicy Bypass -File .\scripts\check_windows.ps1

With the project state reported immediately before P47A, adding these 7 tests should
produce 454 collected tests: 453 passed, 1 skipped (the soak test), assuming no other
local changes were made.

Run
---
powershell -ExecutionPolicy Bypass -File .\scripts\research_retest_anatomy_uni_link_windows.ps1

Default research reference
--------------------------
initial stop      = -1.00% price = -1R
activation        = +1.00R
BE floor          = 0 bps relative to Entry (mechanical research reference only)
horizon           = 72h
runner targets    = +2R / +3R / +5R / +10R
recovery checks   = Entry / +0.25R / +0.50R / +1R / +2R / +3R / +5R / +10R
adverse checks    = -0.25R / -0.50R / -1R

Output
------
reports\retest_anatomy_v1\UNI_LINK_<timestamp>\

summary.md
    Human-readable decision summary.
summary.json
    Full machine-readable summary plus P46 reference cross-check.
be_events.csv
    One row for every +1R activation later stopped at BE, including recovery,
    invalidation, prior-peak reclaim and missed-runner details.
runner_retest_paths.csv
    One row per baseline runner target reached before the original -1R invalidation.
    Contains the deepest retest between first +1R and the eventual target.
be_event_summary.csv
    Activation/BE/invalidation/reclaim statistics by UNI, LINK and pooled.
runner_retest_summary.csv
    Target-specific runner preservation and retest-floor distributions.
recovery_after_be_summary.csv
    How often and how quickly price recovers each level after BE, including whether
    recovery occurred before the original -1R invalidation.
adverse_after_be_summary.csv
    How often and how quickly price reaches -0.25R/-0.50R/-1R after BE.
first_resolution_matrix.csv
    Pairwise first-hit table: recovery (+0.25/+0.50/+1R) versus adverse
    (-0.25/-0.50/-1R).

Mandatory P46 cross-check
-------------------------
For the frozen default UNI/LINK sample, summary.json should report
p46_reference_check.all_match = true.

The reference values are:
+2R baseline runners  = 91
+3R baseline runners  = 68
+5R baseline runners  = 33
+10R baseline runners = 12

For +1R -> BE at 0 bps:
+5R future runners first reached after BE = 7
+10R future runners first reached after BE = 1

If this cross-check fails, do not interpret P47A results until the discrepancy is
explained.

Interpretation rule
-------------------
P47A is anatomy, not optimisation. The purpose is to learn:
1) how deeply eventual runners retest after first reaching +1R;
2) how often BE really precedes original -1R invalidation;
3) how often price reclaims Entry, +0.25R, +0.50R, +1R and the prior peak;
4) how quickly those recoveries occur;
5) whether recovery tends to happen before or after renewed adverse movement.

Only after this report should staged protection and one-shot continuation re-entry
policies be parameterised and compared.
