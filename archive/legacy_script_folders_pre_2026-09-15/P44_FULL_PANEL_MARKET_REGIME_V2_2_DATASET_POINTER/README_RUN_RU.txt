P44 V2.2 — FIX P30 DATASET POINTER

Причина V2.1: legacy P30 поддерживает отдельные --dataset-dir и --output-dir.
Поэтому comparison.json может находиться, например, в одном run-каталоге,
а dataset/trade_5m.csv — в другом. V2.1 ошибочно предполагал, что они соседи.

V2.2 читает канонический dataset_dir прямо из P30 comparison.json.
Никаких market-data download нет. reports installer не меняет. Live trading logic не меняется.

После распаковки в C:\cripta:
  powershell -ExecutionPolicy Bypass -File .\P44_FULL_PANEL_MARKET_REGIME_V2_2_DATASET_POINTER\APPLY_P44_V2_2_DATASET_POINTER_FIX.ps1
  powershell -ExecutionPolicy Bypass -File .\scripts\check_windows.ps1
  powershell -ExecutionPolicy Bypass -File .\scripts\research_market_regime_full_panel_windows.ps1
