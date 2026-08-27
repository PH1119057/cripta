P48 V1.7 CAUSAL WARM-UP + UX

Baseline: installed P48 V1.6 on C:\cripta.
Changes exactly four project files:
  docs\ENTRY_BOT_LIVE_SCANNER_P48_RU.md
  src\bybit_workbench\entry_bot\engine.py
  src\bybit_workbench\ui\main_window.py
  tests\test_entry_bot_live_scanner.py

Changes:
- flow warm-up is based on five ACTUAL completed publicTrade minute buckets, not wall-clock time;
- Entry stays fail-closed if any required 4+1 minute bucket is absent;
- Flow column shows TAPE n/5 during warm-up;
- top Bot panel shows Ready/Warm-up/Error counts and explains what the user is waiting for;
- WS reconnect still clears tape and therefore restarts causal warm-up.

Does NOT change:
- frozen Entry thresholds/calibration;
- P46;
- Exit/Risk;
- authenticated execution;
- Mainnet Auto Entry remains LOCKED.

Install from C:\cripta:
  powershell -ExecutionPolicy Bypass -File .\ENTRY_BOT_LIVE_SCANNER_P48_V1_7_CAUSAL_WARMUP_UX\APPLY_ENTRY_BOT_P48_V1_7.ps1

Then authoritative gate:
  powershell -ExecutionPolicy Bypass -File .\scripts\check_windows.ps1
