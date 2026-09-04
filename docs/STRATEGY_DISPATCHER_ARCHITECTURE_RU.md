# ДИСПЕТЧЕР СТРАТЕГИЙ — АРХИТЕКТУРНЫЙ КОНТРАКТ

**Документ:** `STRATEGY_DISPATCHER_ARCHITECTURE_RU.md`
**Версия:** 1.1
**Дата:** 2026-09-05
**Статус:** канонический специализированный контракт

Верхние контракты:

- `../CRIPTA_ARCHITECTURE_RULES_RU_V1.md`
- `PROJECT_ARCHITECTURE_RU.md`

## 1. Назначение

Dispatcher — второй уровень прикладного торгового контура:

```text
MAYAK -> DISPATCHER -> STRATEGY
```

Он публикует общую обстановку, необходимую Strategy и Entry.

Он не торгует.

## 2. Два класса выходных показателей

Dispatcher публикует:

1. `MarketSuitabilityAssessment` — пригодность текущей рыночной среды для конкретного strategy profile;
2. `TradingCapacitySnapshot` — фактическое состояние торговой ёмкости подключённого аккаунта.

Они не должны сливаться в один status.

Плохой рынок и отсутствие свободных денег — разные причины.

## 3. Рыночный вход

Источник:

```text
MAYAK SharedMarketContext
+
StrategyMarketProfile
```

Результат:

```text
MarketSuitabilityAssessment
```

## 4. Account-capacity вход

Фактический private account state получает технический exchange/account-sync.

Dispatcher архитектурно потребляет нормализованный `TradingAccountState`.

Минимальный contract:

```text
account_state_id
exchange/account identity
observed_at
freshness
total/equity
used
reserved
free
available_for_new_trading
completeness/quality
```

Результат Dispatcher:

```text
TradingCapacitySnapshot
```

## 5. Exchange neutrality

`TradingAccountState` не должен быть моделью одной конкретной биржи.

Adapter каждой площадки преобразует её поля в общий contract там, где это возможно.

Если показатель невозможно честно получить/нормализовать, он остаётся unknown/unsupported, а не подставляется как ноль.

## 6. Dispatcher не владеет средствами

Dispatcher:

- не переводит деньги;
- не резервирует капитал по собственной policy;
- не выбирает allocation;
- не меняет leverage;
- не создаёт/отменяет orders;
- не закрывает positions.

Он только показывает состояние.

## 7. Strategy profile

Market profile описывает требуемую среду.

Он не содержит limit price, size, stop, TP или allocation.

Эти параметры принадлежат Strategy.

## 8. TradingCapacitySnapshot не является разрешением на вход

Даже если:

```text
available_for_new_trading > requested_amount
```

Dispatcher не говорит «войти».

Strategy/Entry принимает решение.

## 9. Причинный audit

Если Entry отказал из-за market assessment:

```text
REASON=DISPATCHER_MARKET_INCOMPATIBLE
consumed_assessment_id=<id>
```

Если Entry отказал из-за account capacity:

```text
REASON=INSUFFICIENT_AVAILABLE_FUNDS
consumed_trading_capacity_snapshot_id=<id>
```

Обе attempts продолжают жить в lifecycle.

## 10. Неограниченное количество Strategy

Один Market snapshot и один account-capacity snapshot могут быть прочитаны многими Strategy/Entry consumers.

Они могут принять разные решения.

## 11. Global Market State

Общий indicator рынка допустим как advisory context.

Он не является `BLOCK_NEW_ENTRIES` или `CLOSE_ALL` mutation.

## 12. Безопасность

Dispatcher не получает прямого пути к Execution mutations.

Technical fail-closed право находится в operational safety / Execution, если mandatory exchange/account state stale/unknown.

## 13. Детерминированность и версии

Market assessment детерминирован относительно MAYAK snapshot, Dispatcher version и profile version.

Trading-capacity snapshot хранит account_state_id, source/adapter version, freshness/completeness и Dispatcher version.

## 14. Главная формула

> MAYAK описывает рынок.

> Dispatcher показывает, насколько этот рынок подходит разным Strategy, и сколько фактической торговой ёмкости сейчас доступно.

> Strategy определяет правила торговли и потребность в капитале.

> Entry принимает конкретное решение.

> Execution исполняет.
