ENTRY P31 RUNTIME V4 SAFE

This patch changes only three research-runtime files:
- scripts/research_entry_full_panel_windows.ps1
- scripts/research_flow_reversal_90d_windows.ps1
- src/bybit_workbench/research/flow_reversal_v1.py

It does NOT replace reports, docs, tests, or live trading logic.

Fixes:
- P31 Python output is unbuffered.
- P31 reports day progress and gzip archive progress with rows, compressed bytes, percent, elapsed and ETA.
- Full-panel heartbeat calls 21/90 what it really is: panel_stages, not P31 days.
- Raw trade timestamps/prices use compact array('d') instead of Python float-object tuples.
- CSV raw tape uses csv.reader instead of DictReader.
- Timestamp parser, Decimal price parsing, exact-touch rules, 6h horizon and thresholds are unchanged.

Stop current run first. If console is in selection mode, press Esc then Ctrl+C.
Then from C:\cripta run:

powershell -ExecutionPolicy Bypass -File .\ENTRY_P31_RUNTIME_V4_SAFE\APPLY_P31_RUNTIME_V4.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\check_windows.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\research_entry_full_panel_windows.ps1

Do not use -AllowDownload.
