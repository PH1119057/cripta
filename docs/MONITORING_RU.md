# MONITORING — активный контракт наблюдения и UI

**Версия:** 1.0  
**Дата:** 2026-09-18  
**Статус:** активный документ поддерживающего контура

# 1. Два разных смысла мониторинга

Нельзя смешивать:
1. сбор/наблюдение рыночных данных по широкому universe;
2. Strategy-specific мониторинг условий торговли.

# 2. Market/coin monitoring

Технический market monitor может наблюдать больше монет, чем торгует конкретная
Strategy.

Наличие symbol в monitor не разрешает торговлю, не включает Strategy, не создаёт
allocation и не является Entry condition.

# 3. Strategy Monitor

Strategy Monitor отображает состояние как минимум в координате:

```text
Strategy version × symbol × direction
```

Один symbol может иметь несколько строк разных Strategy с разными Entry и
состоянием.

Trading universe конкретной Strategy определяется только её StrategyCard.

# 4. UI

UI/read-model показывает факты, позволяет владельцу управлять разрешёнными
control-сущностями, не содержит скрытую торговую policy, не пересчитывает Entry
независимо от канонического runtime и неизвестное показывает как неизвестное.

# 5. Position monitoring

Position Supervisor наблюдает фактическую позицию и может сохранять:
- current price;
- MFE/MAE;
- protection;
- current H9/H3/other Strategy geometry snapshots;
- смещения;
- current MAYAK/Dispatcher context.

Supervisor не получает право самостоятельно изменить Exit policy.
