P44 FULL PANEL MARKET REGIME V2

Purpose:
- next research stage after completed Entry V1 full panel;
- local frozen data only;
- no network downloads;
- S1 calibration, S2+S3 OOS;
- no live logic changes.

Install from C:\cripta:
  powershell -ExecutionPolicy Bypass -File .\P44_FULL_PANEL_MARKET_REGIME_V2\APPLY_P44_FULL_PANEL_V2.ps1

Then:
  powershell -ExecutionPolicy Bypass -File .\scripts\check_windows.ps1

If checks pass:
  powershell -ExecutionPolicy Bypass -File .\scripts\research_market_regime_full_panel_windows.ps1

Default output:
  C:\cripta\reports\market_regime_p44_full_panel\ENTRY_V1_20260518_20260816

The script also creates a small ZIP with result CSV/JSON/MD files next to that output directory.
