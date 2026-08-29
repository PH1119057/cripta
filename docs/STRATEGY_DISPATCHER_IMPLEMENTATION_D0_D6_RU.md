# ДИСПЕТЧЕР СТРАТЕГИЙ — РЕАЛИЗАЦИОННЫЙ КОНТРАКТ D0–D6

**Версия реализации:** 0.2.0  
**Baseline:** `cripta_project_trading_3d_20260829_133108.zip`

## Состав пакета

```text
production/src/bybit_workbench/strategy_dispatcher/
    contracts.py
    vocabulary.py
    engine.py
    registry.py
    adapter.py
    profile_io.py
    serialization.py
    storage.py
    service.py
    provider.py
    replay.py
    cli.py
```

## Каналы данных

Единственный runtime input Диспетчера:

```text
снимок Маяка
```

Дополнительный configuration input:

```text
версионированные профили рыночной среды
```

Запрещённые inputs:

- торговые сигналы как признаки рынка;
- открытые позиции;
- PnL;
- баланс;
- исполненные торговые команды;
- private API state.

## Канонический feature handoff

Маяк в конечной архитектуре предпочтительно публикует отдельный строгий блок
`dispatcher_handoff`, внутри которого находится `dispatcher_features`.
Полный диагностический JSON Маяка может содержать любые собственные поля за пределами
этого handoff.

Это позволяет избежать ситуации, когда Диспетчер повторно анализирует сырые
биржевые потоки и превращается во второй Маяк.

## Legacy bridge

Legacy bridge существует только для старого снимка и диагностики.
Он не считается каноническим handoff.

Его качество ограничено LOW независимо от legacy `confidence`, потому что старый
Маяк ещё не реализует окончательный контракт источников/ликвидаций/событий.

## Хранение

D4 использует простой независимый file store:

- append-only `assessments.jsonl`;
- atomic `status.json`.

Это сознательно не требует миграции PostgreSQL в момент установки основы.
Если позже понадобится PostgreSQL, БД должна быть дополнительным persistence
adapter, а не зависимостью ядра.

## Service safety

Пассивный сервис:

- не имеет сетевого клиента;
- не знает API-ключей;
- не имеет зависимости от Execution;
- читает Mayak status;
- читает profiles;
- пишет только собственное state directory.

## D6

D6 в этой версии означает **готовность read-only consumer contract**, а не
разрешение стратегии использовать его в live.

Фактическое изменение поведения стратегии остаётся отдельным будущим решением
SHADOW -> MICRO_LIVE -> LIVE.
