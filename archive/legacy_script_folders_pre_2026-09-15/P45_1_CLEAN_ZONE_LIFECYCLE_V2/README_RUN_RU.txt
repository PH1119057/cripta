P45.1 CLEAN ZONE LIFECYCLE

1) Распаковать папку P45_1_CLEAN_ZONE_LIFECYCLE_V2 прямо в C:\cripta
2) Из C:\cripta выполнить:

powershell -ExecutionPolicy Bypass -File .\P45_1_CLEAN_ZONE_LIFECYCLE_V2\APPLY_P45_1_CLEAN_ZONE_LIFECYCLE_V2.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\check_windows.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\research_clean_zone_lifecycle_windows.ps1

Результат:
C:\cripta\reports\clean_zone_lifecycle_p451\ENTRY_V1_20260518_20260816.zip

Сеть не используется. Live / Exit / Risk не изменяются.

V2 исправляет Ruff precheck из первой сборки: SIM108 в модуле и I001 в тесте.
