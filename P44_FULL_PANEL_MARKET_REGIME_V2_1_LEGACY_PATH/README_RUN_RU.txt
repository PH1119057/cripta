P44 FULL PANEL MARKET REGIME V2.1 — LEGACY 5M PATH FIX

Назначение:
- исправляет только поиск frozen trade_5m.csv для активов, материализованных из legacy Entry research;
- не копирует тяжёлые датасеты;
- не скачивает данные;
- не меняет Entry V1 и live trading logic.

Причина:
UNI/LINK были перенесены в cross_asset_validation как small report files only, поэтому
p30\dataset\trade_5m.csv отсутствует в новом validation root, хотя исходный frozen dataset
остаётся в reports\entry_research_v3\<SYMBOL>_*\dataset\trade_5m.csv.

После установки:
  powershell -ExecutionPolicy Bypass -File .\scripts\check_windows.ps1
  powershell -ExecutionPolicy Bypass -File .\scripts\research_market_regime_full_panel_windows.ps1
