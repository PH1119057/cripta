ENTRY BOT LIVE SCANNER P48 V1.2 MYPY FIX
============================

Назначение: production live-screening frozen Entry V1 для 10 рабочих монет
с BTC/ETH как reference-only. Этот проход НЕ отправляет автоматические Mainnet ордера.

Установка из C:\cripta:

  powershell -ExecutionPolicy Bypass -File .\ENTRY_BOT_LIVE_SCANNER_P48_V1_2_MYPY_FIX\APPLY_ENTRY_BOT_LIVE_SCANNER_P48_V1_2.ps1

После установки:

  powershell -ExecutionPolicy Bypass -File .\scripts\check_windows.ps1

Когда P35 готов для всех 10 рабочих монет:

  powershell -ExecutionPolicy Bypass -File .\scripts\build_entry_bot_calibration_windows.ps1 -RequireAll

Затем запустить Workbench обычным способом, включить в левой панели
"BOT MODE · 10 монет". Scanner выполняет REST warm-up, затем live screening.

P46, Exit/Risk и существующий Mainnet mutation gateway установщик не изменяет.
