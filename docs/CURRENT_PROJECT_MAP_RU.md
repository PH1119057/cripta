# Текущее устройство и архитектурные границы проекта CRIPTA

**Документ:** `CURRENT_PROJECT_MAP_RU.md`
**Версия документа:** 4.0
**Дата:** 2026-09-05
**Статус:** краткая текущая карта; не отдельный архитектурный контракт

## 1. Source checkpoint

Текущий source checkpoint определяется фактически проверенным равенством:

```text
GitHub PH1119057/cripta:main
==
/srv/cripta/source_checkout
```

Последний runtime checkpoint хранится отдельно и не считается автоматически текущим состоянием.

## 2. Верхняя прикладная архитектура

```text
MAYAK
  ↓
DISPATCHER
  ↓
STRATEGY
 ├─ ENTRY
 └─ EXIT
  ↓
EXECUTION
  ↓
EXCHANGE
```

Пять верхних уровней.

`Risk` не является отдельным верхним слоем.

## 3. Два контура

### Прикладной

Определяет смысл: что происходит на рынке, какова общая обстановка, какая Strategy policy применяется, разрешён ли вход, как сопровождать позицию и какое действие требуется.

### Технический поддерживающий

Обеспечивает market/account connectivity, exchange adapters, private account sync, clock/reconnect, PostgreSQL, IDs/audit, Position Supervisor, Analyst, UI/read models, services, restart/reconciliation и operational safety.

Технический контур поддерживает прикладной, но не становится владельцем Strategy.

## 4. MAYAK

Наблюдает внешний рынок. Trading effect: `NONE`.

## 5. Dispatcher

Публикует два типа показателей:

1. пригодность рыночной среды для профилей Strategy;
2. состояние торговой ёмкости аккаунта.

Account capacity минимум:

```text
total
used
reserved
free
available_for_new_trading
freshness
source_exchange/account
```

Источник фактов — подключённая торговая площадка через technical account-sync.

## 6. Strategy

Owner-approved versioned policy.

Внутри:

```text
ENTRY
EXIT
```

Strategy определяет размер, allocation, leverage, stop, допустимую просадку, holding и exit policy.

## 7. Entry

Monitor/Scanner даёт candidate signal, не приказ на вход.

Entry рассматривает signal в рамках подходящей утверждённой Strategy.

Он может использовать Dispatcher market assessment, Dispatcher account-capacity snapshot и technical readiness.

Отказ из-за отсутствия денег:

```text
INSUFFICIENT_AVAILABLE_FUNDS
```

## 8. Exit

После fill работает по той же Strategy binding.

## 9. Execution

Исполняет готовое решение и владеет exchange mutation mechanics, fill truth, IDs, protection, reconciliation и durable handoff.

## 10. Exchange

Внешняя торговая площадка. Архитектура не привязана к конкретному провайдеру.

## 11. Signal / Attempt

История начинается на `SIGNAL_DETECTED`.

```text
signal_id
-> strategy_attempt_id
-> strategy binding
-> Entry decision
-> optional Execution
-> optional position
-> optional Exit
```

Rejected/no-fill/no-funds attempts сохраняются.

## 12. Аналитика

Supervisor/Analyst/PostgreSQL/UI находятся в поддерживающем наблюдательно-аналитическом контуре.

Они не являются новыми trading layers.

## 13. Масштабирование

Архитектура допускает много Strategy/bots/positions.

Не определены и не должны придумыватьcя без отдельной задачи:

- strategy selector;
- capital allocator;
- strategy priority;
- global position cap.

## 14. Что читать

1. `CRIPTA_ASSISTANT_WORK_RULES_RU_V1.md`
2. `CRIPTA_ARCHITECTURE_RULES_RU_V1.md`
3. `docs/PROJECT_ARCHITECTURE_RU.md`
4. `docs/PROJECT_GOVERNANCE_RU.md`
5. затрагиваемые специализированные контракты
