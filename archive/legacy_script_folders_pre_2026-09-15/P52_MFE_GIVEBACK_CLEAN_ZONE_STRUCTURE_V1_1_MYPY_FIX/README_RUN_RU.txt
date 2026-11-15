P52 MFE + GIVEBACK + CLEAN ZONE STRUCTURE V1.1 MYPY FIX

1. Распаковать архив в C:\cripta
2. Применить:
   powershell -ExecutionPolicy Bypass -File .\P52_MFE_GIVEBACK_CLEAN_ZONE_STRUCTURE_V1_1_MYPY_FIX\APPLY_P52_MFE_GIVEBACK_CLEAN_ZONE_STRUCTURE_V1_1_MYPY_FIX.ps1
3. Обязательный Windows gate:
   powershell -ExecutionPolicy Bypass -File .\scripts\check_windows.ps1
4. Запустить research:
   powershell -ExecutionPolicy Bypass -File .\scripts\research_mfe_giveback_clean_zone_p52_windows.ps1

Результат:
C:\cripta\reports\mfe_giveback_clean_zone_p52\ALL9_P52_WORKING

Главные файлы:
- first_structure_summary.csv
- first_structure_sign_summary.csv
- structure_balance_summary.csv
- entry_zone_followup_summary.csv
- mfe_giveback_structure_stop_tradeoff.csv
- structure_stability.csv
- summary.md / summary.json

NEW5 не открываются. Live Entry/Exit/Risk/Execution не меняются.

V1.1 исправляет только mypy precheck V1. Логика research не изменена. V1 не был применён из-за fail-closed, поэтому rollback не нужен.
