ENTRY BOT P48 V1.6 TEST CONTRACT FIX

Baseline: installed P48 V1.2 on current C:\cripta. V1.4/V1.5 attempts were fail-closed and did not modify project files.

Changes:
- preserves V1.5 warm-up resilience: explicit scanner start, no-calibration REST skip, transient REST retries, per-asset warm-up failure isolation;
- fixes GUI test contract so the fake scanner is STOPPED before the explicit Start click and RUNNING only after the callback;
- adds assertions that Start is enabled before launch and disabled after launch.

Does NOT change:
- frozen Entry V1 decision thresholds;
- P46/fingerprint;
- Exit/Risk;
- automatic Mainnet Entry remains LOCKED.

Install from C:\cripta:
  powershell -ExecutionPolicy Bypass -File .\ENTRY_BOT_LIVE_SCANNER_P48_V1_6_TEST_CONTRACT_FIX\APPLY_ENTRY_BOT_P48_V1_6.ps1

Then authoritative gate:
  powershell -ExecutionPolicy Bypass -File .\scripts\check_windows.ps1
