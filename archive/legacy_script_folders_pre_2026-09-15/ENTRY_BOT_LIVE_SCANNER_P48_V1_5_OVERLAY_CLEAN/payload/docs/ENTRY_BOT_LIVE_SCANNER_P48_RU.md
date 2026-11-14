# P48 — Entry Bot Live Scanner V1

## Назначение

P48 переносит frozen Entry V1 из offline research в отдельный live-скринер приложения.
Он постоянно наблюдает торговую десятку, формирует causal Core-сигналы и сохраняет их
в SQLite. Автоматические Mainnet-заявки в этом проходе намеренно заблокированы:
сначала live scanner должен пройти production-equivalence gate относительно research.

P46 confirmatory holdout этим модулем не изменяется и не читается.

## Торговая вселенная

Рабочие инструменты:

- UNIUSDT
- LINKUSDT
- XRPUSDT
- SOLUSDT
- ADAUSDT
- BNBUSDT
- AVAXUSDT
- SUIUSDT
- AAVEUSDT
- LTCUSDT

Reference-only: BTCUSDT и ETHUSDT. Они не могут породить торговый Entry Bot сигнал.
1000PEPEUSDT и DOGEUSDT не входят в рабочую десятку.

## Runtime

`EntryBotRuntime` не имеет API-ключей, приватного WebSocket и метода размещения ордера.
Он использует только public REST/WS текущего endpoint profile.

При запуске для каждой рабочей монеты загружается causal warm-up:

- 5m closed candles;
- 15m closed candles;
- 60m closed candles;
- 5m open interest history.

После этого один public WebSocket подписывается на рыночные темы рабочей десятки и
BTC/ETH reference. После запуска или reconnect public-trade flow считается неготовым
минимум 5 полных минут; Entry в этот период fail-closed.

## Frozen Entry V1 mechanics в live scanner

Hard Core gate переносит текущую frozen механику:

1. causal 5m support/resistance zone;
2. causal 15m zone;
3. confluence gap <= 0.25%;
4. exact public-trade touch;
5. 4 завершённые минуты adverse pressure;
6. непосредственно предыдущая завершённая минута reversal;
7. 60m failure embargo;
8. frozen asset-specific P35 OI-tail thresholds;
9. OI-tail danger запрещает Core signal.

Текущая touch-минута никогда не участвует в flow features.

## Важное отличие live от исторического P30

Исторический P30 знает полный OHLC 5m бара и исключает бар, если в нём были задеты
одновременно LONG и SHORT уровни. Live-процесс не может знать будущее бара. Поэтому
P48 использует причинное правило `first touch wins` и помечает сигнал
`first_touch_live_convention=true`.

Именно это отличие должно быть количественно проверено production-equivalence gate до
разрешения автоматических Mainnet Entry. Нельзя незаметно считать live first-touch
полностью эквивалентным историческому P30.

## OI calibration

Production runtime не пересчитывает quantile thresholds на лету. Он читает компактный
`var/entry_bot_calibration.json`, построенный из уже завершённых P35 `summary.json`.

Команда:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_entry_bot_calibration_windows.ps1 -RequireAll
```

Если хотя бы у одной из десяти монет нет frozen P35 summary, `-RequireAll` завершится
ошибкой. Без calibration конкретная монета остаётся `NO CALIBRATION`, а Entry fail-closed.

## Bot Mode UI

В левой панели добавлен переключатель `BOT MODE · 10 монет`.

Таблица показывает:

- монету;
- состояние scanner;
- ожидаемую сторону ближайшего armed level;
- расстояние до уровня;
- последнее flow-state;
- OI-state.

Статусы: `WARMUP`, `NO CALIBRATION`, `WAITING`, `WATCH`, `APPROACH`, `COOLDOWN`,
`BLOCKED`, `SIGNAL`, `ERROR`.

Двойной щелчок по строке переводит основной интерфейс на выбранный symbol.

## Durable Entry -> Exit handoff

SQLite schema v8 добавляет две таблицы:

- `entry_bot_signals` — idempotent journal Core signals;
- `position_handoffs` — durable ownership boundary между Entry и Exit.

Контракт следующий:

1. scanner создаёт Core signal и пишет его в `entry_bot_signals`;
2. будущий Auto Entry Executor проводит risk/execution gate и отправляет ордер;
3. только после подтверждённого fill **и подтверждённой initial protection** Executor
   публикует `PositionHandoff(state=OPEN)`;
4. Exit runtime атомарно `claim_next(consumer_id)` и становится владельцем позиции;
5. Entry scanner продолжает искать другие сделки и не пытается управлять переданной
   позицией;
6. после завершения позиции Exit переводит handoff в `CLOSED`.

Таким образом Exit не должен угадывать факт входа по потоку сигналов или повторно
скринить первоначальную Entry механику.

## Что P48 намеренно НЕ делает

- не отправляет ордера;
- не меняет существующий Mainnet mutation gateway;
- не меняет Exit/Risk;
- не меняет P46;
- не применяет P44/P45.1 discovery-факторы как hard gates;
- не включает directional 1H hard filter.

Следующий production-проход после получения calibration по новой пятёрке:

1. historical/live production-equivalence replay;
2. измерение расхождений first-touch против full-bar P30;
3. отдельный Auto Entry Executor через существующие fail-closed Mainnet safety gates;
4. MICRO_LIVE;
5. публикация durable handoff в Exit только после fill + protection confirmation.

## P48.1: warm-up resilience and explicit start

- BOT MODE only switches the left panel; screening starts only after the operator presses
  `Запустить скрининг`.
- Assets without OI calibration are `NO CALIBRATION` and are skipped by REST warm-up.
- REST warm-up retries transient TLS/read/network timeouts up to four attempts per request.
- If one calibrated asset still cannot warm up after retries, it remains fail-closed with `ERROR`,
  while successfully warmed assets continue into public WebSocket screening.
- Automatic Mainnet Entry remains locked.
