# Следующая задача Codex: стратегии, честный BackTest и Mainnet Shadow

Дата ревью: 14 августа 2026.  
Основание: текущий исходный код проекта и спецификации:

- `docs/strategies/algorithm_1_trend_breakout.md`;
- `docs/strategies/algorithm_2_power_channel_rejection.md`.

## 1. Итог ревью текущего среза

Каркас достаточно зрелый, чтобы подключать стратегии. Особенно хорошо реализованы:

- граница Strategy -> Risk Gate -> Execution -> Bybit adapter;
- fail-closed состояния, freshness и reconciliation;
- Mainnet safety gateway с режимами `SHADOW`, `MICRO_LIVE`, `LIVE`;
- идемпотентность через intent/orderLinkId и durable execution FSM;
- обязательная серверная защита и аварийный reduce-only close;
- `Decimal`, UTC, tick/qty-нормализация и risk-based sizing;
- детерминированный Replay с next-bar execution, gap-through-stop, partial fills и
  консервативной ambiguous candle;
- исторические fingerprints, chronological split, walk-forward primitives,
  stress primitives и persisted eligibility report;
- защита секретов и запрет withdrawal endpoint.

Проверка в чистом временном окружении:

```text
pytest: 202 passed, 1 skipped
ruff:   all checks passed
mypy:   no issues in 82 source files
```

Пропущены только GUI smoke без установленного PySide6 и opt-in soak. Сетевые вызовы
и торговые команды во время ревью не выполнялись.

Архив исходно включал `.venv`, caches, build/dist и тестовые SQLite/PNG из `var`
и занимал около 309 MB. Это не дефект торговой логики, но такие файлы нельзя включать
в рабочую передачу исходников и тем более в репозиторий.

## 2. P0-разрывы перед автоматической стратегией

### 2.1 Автоматический runtime ещё не подключён

`StrategyArmingService` и универсальный historical runner существуют, но production-
потока, который создаёт автоматическую стратегию, прогревает её, передаёт закрытые
свечи, обрабатывает intents и восстанавливает состояние, пока нет. UI фактически
работает через ручной workflow.

Реализован единый `AutomaticStrategyRuntime` для Replay, Mainnet Shadow и Mainnet
execution с одним объектом стратегии и одинаковой последовательностью событий.
В `SHADOW` intent никогда не передаётся write-sink; transport также независимо
блокирует любую мутацию.

### 2.2 Контекст стратегии недостаточен для безопасного состояния

`ReadOnlyStrategyContext` сейчас содержит только symbol, latest price, position и
parameters. Для двух стратегий необходимы как минимум read-only snapshots:

- текущий confirmed hard stop/TP/trailing;
- pending entry: client id, side, price, original/remaining quantity, status, age;
- последняя подтверждённая execution с полной ценой, quantity и timestamp;
- текущая Mark Price отдельно от Last Price;
- engine health / право создавать новые входы;
- восстановленный strategy state version.

`on_execution()` должен получать типизированный `Execution`, а не только строковый id.
Стратегии также нужен callback результата каждого intent: approved/rejected/submitted/
cancelled/unknown. Нельзя считать emitted intent исполненным и строить локальное
состояние на предположении.

### 2.3 Warm-up и восстановление

Перед разрешением сигналов runtime обязан:

1. получить закрытую историю;
2. проверить continuity и timeframe;
3. прогреть ATR/канал без создания intents;
4. восстановить persisted state;
5. выполнить reconciliation pending order/position/protection;
6. только затем разрешить новые сигналы.

Дубликат последней свечи после reconnect не должен повторять signal/intent.

### 2.4 Параметры пока только описаны, но не валидируются полностью

`StrategyRegistration.resolve_parameters()` запрещает неизвестные имена, но не
проверяет фактический тип, диапазон и межпараметрические ограничения. Historical
runner объединяет defaults с пользовательскими значениями напрямую и тем самым
может обойти registry validation.

Нужно иметь одну типизированную parameter schema на strategy/version и применять её
одинаково в UI, CLI, Replay, report fingerprint и Mainnet arming.

### 2.5 Текущий runner однотаймфреймовый

`DataRequirements.timeframes` сейчас трактуется как перечень допустимых значений,
а `run_strategy()` получает один `HistoricalDataset`. Настоящего объединения нескольких
таймфреймов нет. Поэтому обе спецификации v0.1 сознательно однотаймфреймовые.

Не добавлять скрытый 4h-фильтр к 1h-сигналу, пока не появятся причинная агрегация,
watermark и тесты отсутствующих/запаздывающих HTF-свечей.

## 3. P0-разрывы BackTest и реального исполнения

### 3.1 Mark Price для защиты

Mainnet adapter ставит stop/TP с `slTriggerBy=MarkPrice`, а Replay проверяет защиту
по OHLC обычной торговой свечи. Это разные ценовые ряды.

Нужно расширить historical dataset синхронными Mark Price candles и срабатывать по
ним, сохраняя исполнение защитного market-order по явно документированной модели.
Отчёт без Mark Price ряда не должен получать production-equivalent флаг.

### 3.2 Funding только учитывается, но не подаётся

`ReplayEngine.apply_funding()` и поле metrics существуют, однако historical runner
не загружает и не применяет funding events. В обычном backtest funding всегда останется
нулевым. Проверка `execution_costs_modelled` сейчас требует только ненулевые fee и
slippage.

Нужно добавить timestamped funding series, корректную сторону платежа, фактический
размер открытой позиции в момент settlement и отдельный report check. Отсутствие
funding-данных для perpetual должно быть явно видно и блокировать строгий gate.

### 3.3 Семантика входа должна совпадать

Mainnet execution обычный вход сейчас только GTC-limit. Поэтому historical eligibility
для v0.1 строится только по limit-retest правилам из спецификаций. Нельзя пропускать
в Micro-Live конфигурацию, проверенную как market-at-next-open.

Если позднее реализуется marketable IOC-limit со slippage cap, это новый execution
mode, новая версия стратегии/исполнителя и отдельный historical report.

### 3.4 Maker/taker и частичное исполнение

Replay использует один fee rate. Нужно уметь явно задавать maker/taker ставки и
классифицировать fill по модели исполнения. `max_fill_quantity_per_bar` полезен как
стресс-примитив, но не является моделью ликвидности; отчёт обязан это говорить.

### 3.5 Historical gate пока слишком слаб для сравнения гипотез

Текущий gate проверяет minimum trades, абсолютный drawdown, ambiguous fraction,
положительный OOS PnL и ненулевые fee/slippage. Он не требует:

- успешных walk-forward folds;
- stress-сценариев;
- относительного drawdown к equity;
- устойчивости соседних параметров;
- funding/Mark Price completeness;
- совпадения execution mode;
- учёта количества проверенных конфигураций.

Для Micro-Live сохраняется обязательный gate, но отчёт должен показывать все эти поля.
Для Live нужен отдельный более строгий policy; положительный PnL сам по себе
никогда не является достаточным условием.

## 4. Порядок реализации

### Этап 1 — расширить контракт стратегии

- Добавить типизированные snapshots и intent outcome callback.
- Добавить versioned, JSON-safe strategy state snapshot/restore.
- Добавить зависимый от параметров warm-up requirement.
- Сделать одну parameter validation boundary.
- Сохранить лимит intent id до 36 символов и детерминированную дедупликацию.

### Этап 2 — реализовать чистые стратегии

- `user_algorithm_1` строго по `algorithm_1_trend_breakout.md`.
- `user_algorithm_2` строго по `algorithm_2_power_channel_rejection.md`.
- Индикаторные расчёты вынести в чистые функции без UI/Bybit/import side effects.
- Каждая стратегия — отдельная сериализуемая машина состояний.
- Не добавлять оптимизатор, ML, regime switch или новые фильтры.

### Этап 3 — golden и counterexample tests

Для каждой формулы нужны табличные тесты Long/Short, равенства границ, warm-up,
expiry, partial fill, restart, duplicate bar, out-of-order, no-repaint и monotonic stop.

Обязательный metamorphic test: добавление будущих свечей к dataset не меняет уже
зафиксированные решения и snapshot предыдущих сигналов.

### Этап 4 — довести historical data model

- Trade OHLCV + Mark Price OHLC + funding events с общим timeline.
- Явные data quality flags и fingerprints каждого ряда.
- Maker/taker fee assumptions.
- Отдельные отчёты missing data и forced end-of-test flatten.
- Один и тот же strategy/runtime adapter для Replay, Mainnet Shadow и execution.

### Этап 5 — BackTest CLI/UI

Минимально предоставить:

- выбор strategy/version, symbol, timeframe, dataset и parameters;
- train/validation/final-test границы без перемешивания;
- запуск base, walk-forward и заранее заданных stress cases;
- просмотр каждой сделки, signal snapshot, intent, risk decision, fill, protection;
- экспорт JSON/CSV отчёта;
- повторный запуск по report manifest с тем же fingerprint/seed;
- явный бейдж `Research only` / `Eligible for Micro-Live`, но никогда `profitable`.

### Этап 6 — AutomaticStrategyRuntime в Replay и Mainnet

До реального Micro-Live запуска должны пройти:

1. полный unit/property suite;
2. golden/counterexample suite обеих стратегий;
3. выбранная стратегия и точный parameters fingerprint проходят historical gate;
4. Replay shadow-run на свежих свечах;
5. Mainnet GET-only connection test и Shadow sync;
6. отдельный ручной opt-in пользователя на минимальный Micro-Live smoke.

BackTest и перезапуск приложения никогда не включают execution. `LIVE` во время
разработки и тестов не активировать.

## 5. Требования к BackTest-отчёту

Помимо существующих метрик добавить:

- initial/ending equity и return percent;
- max drawdown amount и percent;
- expectancy в деньгах и в R;
- average/median win, loss и holding time;
- payoff ratio, longest loss streak;
- signal count, skipped/rejected/cancelled/expired entry count;
- limit fill rate и partial-fill fraction;
- PnL отдельно до fees, fees, slippage estimate и funding;
- результаты по месяцам/кварталам и каждому walk-forward fold;
- sensitivity table соседних параметров;
- выбранный price trigger (`MarkPrice`) и execution mode;
- факт forced flatten в конце периода;
- список всех проверенных parameter fingerprints.

Не рассчитывать Sharpe/Sortino по списку отдельных сделок как будто они равномерный
временной ряд. Если они добавляются, сначала строится регулярная equity return series
с явно указанной частотой и risk-free assumption.

## 6. Начальная матрица экспериментов

Не запускать полный grid search. Сначала одна контрольная конфигурация каждой стратегии
на каждом dataset, затем sensitivity по одному параметру.

| Эксперимент | Стратегия | Timeframe | Параметры |
| --- | --- | --- | --- |
| T1 | Trend Breakout Retest | 60 | defaults |
| T2 | Trend Breakout Retest | 240 | defaults |
| P1 | Power Channel Rejection | 60 | defaults, power filter off |
| P2 | Power Channel Rejection | 240 | defaults, power filter off |
| P3 | Power Channel Rejection | 60 | defaults, power filter on |

Символы задаются пользователем отдельно. Каждый symbol/timeframe имеет собственный
dataset fingerprint и отчёт. Нельзя выбирать лучший из множества вариантов и затем
называть тот же период out-of-sample.

## 7. Definition of done

- Обе стратегии зарегистрированы как `AUTOMATIC`, version `0.1.0`.
- Все параметры валидируются одной схемой во всех режимах.
- Доступ к API/сети из стратегии технически невозможен.
- Каждое решение основано только на уже закрытых данных и имеет frozen snapshot.
- Replay, Mainnet Shadow и execution используют один алгоритмический код.
- Исторические решения неизменны после добавления будущих свечей.
- Limit entry, expiry, partial fill, stop/TP и trailing имеют одинаковую смысловую
  модель в тесте и Mainnet execution.
- BackTest учитывает fees, slippage, Mark Price triggers и funding либо честно
  блокирует строгий gate при отсутствии ряда.
- Обе стратегии имеют отдельные отчёты; автоматический regime switch отсутствует.
- 100% существующих тестов и новые тесты проходят; GUI/soak пропуски документированы.
- Mainnet-профиль проходит последовательный GET-only test: server time,
  `/v5/user/query-api`, balance, positions, open orders.
- UI показывает endpoint, master/subaccount, Read/Write, IP binding, permissions,
  `deadlineDay`/`expiredAt` и arming blockers.
- `SHADOW` transport-level блокирует create/amend/cancel/leverage/margin/stops.
- После перезапуска `MICRO_LIVE` и `LIVE` всегда disarmed.
- Никакие Mainnet API Key/secret не попадают в код, архив, SQLite или отчёты.

## 8. Ключи и внешняя проверка

Пользователь создал Mainnet API-профиль `BotW-Mainnet`. Ключ и secret вводятся только
локально через Windows Credential Manager. Не просить присылать их в чат и не сохранять
в `.env`, конфигурации, SQLite, логи или тестовые данные.

Настраиваемый REST endpoint выбирается явно: `https://api.bybit.com`,
`https://api.bybit.kz` или ручной HTTPS override. Автоматический fallback между
доменами запрещён. `ContractTrade: Order, Position` и Unified Account обязательны.
Любое Wallet/Withdraw/AccountTransfer/SubMemberTransfer право блокирует arming;
SpotTrade/OptionsTrade показываются как предупреждение о лишних правах.

Наличие ключей не разрешает автоматически открывать позицию. Первый Micro-Live smoke
остаётся отдельным ручным действием после завершения реализации и BackTest; Codex
обязан остановиться и запросить подтверждение пользователя.
# HISTORICAL / NOT CURRENT TASK / NOT CURRENT PRODUCTION CONTRACT

Этот документ сохранён для provenance и не является действующим поручением.
