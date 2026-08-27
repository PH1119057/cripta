# PASS 4 — BackTest и exact historical eligibility

Дата: 14 августа 2026.
Версия приложения: `0.4.0`.

## Что закрыто

Проход 4 связывает historical eligibility не просто со стратегией и параметрами, а с
точным воспроизводимым контекстом теста. Автоматическая стратегия не может использовать
отчёт другого инструмента, таймфрейма, версии кода, набора данных или execution model.

Точная привязка включает:

- `symbol` и `timeframe`;
- версию кода Workbench и версию стратегии;
- fingerprint параметров стратегии;
- fingerprint Trade + Mark Price + funding данных;
- maker/taker fee и slippage;
- execution mode и `MarkPrice` trigger;
- fingerprint реальных `InstrumentRules` Bybit.

Binding сохраняется в SQLite вместе с validation report. Старые исторические записи
после миграции остаются доступными для исследования, но из-за отсутствия полной
привязки не могут разрешить Micro-Live.

## Historical runner

- walk-forward и temporal OOS получают только причинный warm-up из прошлого;
- intents, появившиеся во время warm-up, подавляются и не считаются сделками;
- equity curve рассчитывается mark-to-market;
- drawdown учитывает внутрисвечное неблагоприятное движение открытой позиции;
- funding, maker/taker fee и slippage входят в моделирование/отчёт;
- slippage сохраняется по каждому fill;
- dataset fingerprint включает Trade, Mark Price и funding.

## Production-equivalent gate

Eligibility-режим требует:

- точные `InstrumentRules` для выбранного символа;
- полный Mark Price history;
- явно предоставленный непустой funding history;
- `closed-candle-limit-retest` execution model;
- `MarkPrice` как trigger;
- chronological temporal validation с отдельным OOS участком.

GUI получает `InstrumentRules` и текущие maker/taker fee из read-only snapshot. Если
эти данные отсутствуют, production eligibility не создаётся. Research-only BackTest
при этом остаётся доступным.

## Rerun

Формат отчёта повышен до `backtest-report-v2`. Rerun восстанавливает exact rules,
execution settings, Mark/funding inputs и дополнительные validation suites и сравнивает
fingerprints. Отчёты v1 не повышаются в Micro-Live автоматически: для них необходимо
создать новый v2 report текущей версией кода.

## Mainnet safety

Короткоживущий Micro-Live ticket для автоматической стратегии дополнительно проверяет,
что exact historical binding:

- принадлежит текущему символу;
- использует разрешённый таймфрейм `60` или `240`;
- создан текущей версией Workbench;
- совпадает с текущими `InstrumentRules` и комиссиями аккаунта;
- не был изменён после сохранения.

BackTest по-прежнему не включает торговые POST. Live strategy provider остаётся
намеренно не подключён до прохода 5.

## Миграция БД

Schema version: `7`. В `historical_validation_reports` добавлены поля exact binding и
составной индекс для точного поиска eligibility.

## Проверка в среде сборки

В доступной Linux-среде перед упаковкой выполнены `compileall`, headless smoke и
pytest: `236 passed, 2 skipped, 37 subtests passed`. Пропущены только GUI smoke
(PySide6 отсутствует в этой Linux-среде) и soak; файл Hypothesis safety properties
исключён из локального collection, потому что Hypothesis здесь не установлен. Полный
авторитетный прогон Ruff + mypy + pytest + GUI/headless smoke должен быть выполнен на
целевой Windows 10 x64 из `uv.lock`.

Команда на целевой машине:

```powershell
cd C:\cripta
powershell -ExecutionPolicy Bypass -File .\scripts\setup\_windows.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\check\_windows.ps1
```

Ожидаемая финальная строка: `PASS 4 verification completed successfully.`

## Что намеренно осталось закрытым

Проход 5: стратегия/SHADOW runtime, `PENDING_UNKNOWN`, reconciliation неизвестных
заявок, duplicate/out-of-order/restart/reconnect/soak и live strategy provider.

Проходы 6–7: Windows release и реальный GET-only acceptance. Первый реальный
Micro-Live POST остаётся отдельным восьмым действием только после нового ревью и
явного подтверждения владельца аккаунта.
