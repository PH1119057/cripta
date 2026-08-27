ENTRY V1 FULL CROSS-ASSET PANEL — PATCH
=======================================

Что делает патч
---------------
1. Добавляет единый runner для frozen 9-asset Entry V1 validation:
   UNIUSDT, LINKUSDT, BTCUSDT, ETHUSDT, XRPUSDT,
   1000PEPEUSDT (display: PEPE), SOLUSDT, DOGEUSDT, ADAUSDT.
2. Не меняет live trading logic.
3. Не трогает папку reports и не копирует 60–65 ГБ исторических данных.
4. Переиспользует готовые frozen datasets и legacy UNI/LINK reports.
5. По умолчанию fail-closed: если dataset/orderbook cache не найден,
   research не стартует и ничего тяжёлого самовольно не скачивает.
6. Добавляет heartbeat/progress для полного panel runner и P43 orderbook preflight.
7. Создаёт итоговые asset-balanced таблицы: median/IQR/dispersion,
   improved/worsened assets, плюс secondary pooled statistics.
8. P44 Market Regime остаётся в карантине. Новые Entry gates автоматически
   не создаются.

Установка
---------
Распаковать архив в любую временную папку, затем из этой папки:

powershell -ExecutionPolicy Bypass -File .\APPLY_FULL_PANEL_PATCH.ps1

По умолчанию патч ставится в C:\cripta.
Installer:
- проверяет SHA256 payload;
- проверяет синтаксис PowerShell встроенным parser;
- проверяет Python syntax через C:\cripta\.venv\Scripts\python.exe, если он есть;
- делает backup изменяемых файлов в C:\cripta\patch_backups\ENTRY_V1_FULL_PANEL_<timestamp>;
- никогда не меняет reports\.

Проверка проекта после установки
--------------------------------
cd C:\cripta
powershell -ExecutionPolicy Bypass -File .\scripts\check_windows.ps1

Первый запуск full-panel validation
-----------------------------------
ВАЖНО: сначала БЕЗ -AllowDownload:

powershell -ExecutionPolicy Bypass -File .\scripts\research_entry_full_panel_windows.ps1

Это одновременно является безопасным preflight. Если какого-то frozen dataset
или тяжёлого orderbook cache нет, runner остановится ДО research stages и напишет
точный список символов.

НЕ включать -AllowDownload автоматически. Сначала сохранить/прислать точный текст
ошибки preflight. Особенно важно проверить 1000PEPEUSDT: старый P43 script содержал
ошибочный default PEPEUSDT; в этом патче исправлено на 1000PEPEUSDT.

Если preflight проходит, runner начинает 9-asset расчёт и не молчит:
stage, processed/total, %, elapsed, ETA, heartbeat default 20 секунд.

Итоговые файлы
--------------
C:\cripta\reports\cross_asset_validation\ENTRY_V1_FULL_PANEL_20260518_20260816\

Основные:
- panel_summary.md
- panel_summary.json
- panel_asset_summary.csv
- panel_pipeline_asset_layers.csv
- panel_pipeline_transfer.csv
- panel_context_asset_matrix.csv
- panel_context_transfer.csv

Главный принцип интерпретации
-----------------------------
Pooled rate — вторичный показатель.
Основные transfer-метрики: median across assets, dispersion/IQR и число активов,
на которых слой улучшает/ухудшает результат. До просмотра полной матрицы новые
Entry hard rules / veto не добавляются.
