# Bybit Strategy Workbench — runbook

## Установка на чистой Windows

1. Установить Python 3.12+ x64 и создать виртуальное окружение:
   `py -3.12 -m venv .venv`.
2. Активировать его: `.venv\Scripts\Activate.ps1`.
3. Установить приложение: `python -m pip install -e .[dev,build,history]`.
4. Запустить все проверки: `python -m pytest`.
5. Проверить офлайн-сценарий: `python -m bybit_workbench --headless`.
6. Запустить GUI: `python -m bybit_workbench`.

По умолчанию профиль `replay`: сеть и ключи не требуются. Для безопасного Mainnet
Shadow задайте `BYBIT_WORKBENCH_PROFILE=live`. Это включает только Mainnet read path;
write остаётся disarmed и после каждого перезапуска возвращается в `SHADOW`.

## Windows-сборка

Авторитетный release gate для Windows 10/11 x64:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\release\_windows.ps1
```

Он выполняет Ruff, mypy, pytest, opt-in offline soak, чистый one-file PyInstaller build,
packaged headless smoke, packaged offscreen-GUI smoke, SHA-256/PE x64 verification и
создаёт source/release ZIP. UPX отключён.

Для проверки готового bundle на другой чистой Windows x64 Python не нужен: распакуйте
bundle и запустите `verify_clean_windows.ps1`. Скрипт проверяет EXE SHA-256 и повторяет
packaged headless/GUI smoke только в Replay.

## Ключи

- Ключи сохраняются только в Windows Credential Manager через окно приложения.
- Mainnet-профиль называется `BotW-Mainnet`.
- ContractTrade требует только Order/Position и Unified Account. Wallet/withdrawal/
  transfer, Spot и Options/USDC права должны отсутствовать.
- При ротации: остановить приложение, удалить профиль в окне ключей, отозвать старый
  ключ на Bybit, создать новый минимально привилегированный ключ и сохранить его.

## Backup и restore

1. Остановить приложение и убедиться, что процессов Workbench нет.
2. Скопировать `var\workbench.db`, а также существующие `-wal`/`-shm` рядом с ним,
   как единый набор. Надёжнее предварительно выполнить SQLite checkpoint штатным
   инструментом администратора.
3. Хранить backup в зашифрованном пользовательском хранилище. Секретов API в БД нет.
4. Для восстановления переименовать повреждённый набор, вернуть все файлы backup и
   сначала запустить Replay/headless. При Mainnet выполнить GET-only connection test
   до любого Arm.

## Сбой и восстановление

- Неизвестный исход write-команды: не повторять; искать по `orderLinkId` и выполнять
  reconciliation.
- Private WS устарел: новые входы запрещены; REST используется только как fallback.
- Открытая позиция после рестарта: приложение только синхронизируется и сопровождает
  её. Новый вход требует повторного Check/Arm/Run.
- Неподтверждённый hard stop: `EMERGENCY_STOP`, отмена остатка entry и reduce-only
  market close.
- Перед ручным аварийным закрытием сверить символ и позицию в родном интерфейсе Bybit.

## Micro-Live smoke (только явный opt-in)

Автоматически не запускается. До отдельного подтверждения пользователя остановиться.
После подтверждения последовательно:

1. read-only sync и нулевая стартовая позиция;
2. минимальный допустимый limit entry;
3. подтверждение order/execution/position через private WS;
4. подтверждение hard stop и безопасное движение trailing stop;
5. Stop/Cancel остатка partial fill;
6. reduce-only close;
7. REST reconciliation с нулевой позицией и сохранённый журнал.

## Mainnet endpoints и режимы

REST endpoint выбирается без fallback: `https://api.bybit.com`, `https://api.bybit.kz`
или ручной HTTPS override. Первый тест выполняет только GET server time, query-api,
balance, positions и open orders. `MICRO_LIVE` требует ручного `ARM MICRO_LIVE`, caps,
isolated/1x, server-side stop и свежий билет не старше пяти минут. Gateway не меняет
margin mode или leverage: оператор заранее выставляет isolated/1x в интерфейсе Bybit,
а неверное состояние блокирует вход. Full `LIVE` в текущей версии не активируется.

До прохода 7 не выполнять реальный GET-only тест, до отдельного hard stop — Micro-Live.

Mainnet coordinator подключён к desktop workflow и поддерживает подтверждение через
Private WS с REST fallback, partial-fill protection, cancel и reduce-only emergency
close. Exact historical gate требует воспроизводимый BackTest report, совпадающий с
symbol/timeframe, Workbench/strategy version, параметрами, market-data fingerprints,
комиссиями, execution model и InstrumentRules. После прохода 5 стратегии имеют version
`0.2.0`, поэтому старый report для `0.1.0` должен быть пересоздан до Micro-Live.

Это не повод включать соединение или переносить ключи в файлы; профиль остаётся в
Windows Credential Manager. Реальный GET-only acceptance выполняется только в проходе
7, а первый торговый POST остаётся отдельным hard stop после всех семи проходов.
# HISTORICAL / NOT CURRENT TASK / NOT CURRENT PRODUCTION CONTRACT

Legacy Windows Workbench runbook; сохранён для provenance.
