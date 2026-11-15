P46 CONFIRMATORY HOLDOUT V1.1 — PowerShell syntax hotfix

P46 CONFIRMATORY HOLDOUT V1

1) Установить патч.
2) Запустить scripts\check_windows.ps1.
3) СРАЗУ зафиксировать протокол:
   powershell -ExecutionPolicy Bypass -File .\scripts\freeze_p46_confirmatory_holdout_windows.ps1

Holdout начинается 2026-08-19 00:00 UTC и заканчивается 2026-09-18 00:00 UTC.
До окончания периода prepare/evaluate намеренно заблокированы против partial peeking.

После 2026-09-18 00:00 UTC:
   powershell -ExecutionPolicy Bypass -File .\scripts\prepare_p46_holdout_data_windows.ps1
   powershell -ExecutionPolicy Bypass -File .\scripts\research_p46_confirmatory_holdout_windows.ps1

P39/P40/orderbook для P46 не скачиваются и не нужны.
