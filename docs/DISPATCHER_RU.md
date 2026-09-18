# DISPATCHER — активный контракт объективного контекста

**Версия:** 1.0  
**Дата:** 2026-09-18  
**Статус:** активный канонический документ слоя DISPATCHER

# 1. Назначение

Dispatcher находится между MAYAK и Strategy и публикует удобный
strategy-agnostic прикладной контекст.

Он не отвечает на вопрос:
> подходит ли рынок конкретной Strategy?

# 2. Основные классы данных

Dispatcher может публиковать:
1. глобальный контекст рынка;
2. контекст конкретного инструмента/монеты;
3. объективный `CoinMarketRating`, если формула отдельно утверждена;
4. состояние торговой ёмкости аккаунта.

Account capacity минимум различает:
- total/equity;
- used;
- reserved;
- free;
- available_for_new_trading;
- freshness;
- source exchange/account.

# 3. Границы

Dispatcher не читает PnL Strategy как рыночный признак, не знает Entry formula,
не создаёт Strategy profiles/suitability, не включает/выключает Strategy, не
выбирает LONG/SHORT, не создаёт StrategySignal, не резервирует средства по
своей воле, не отправляет ордер и не закрывает позиции.

# 4. Strategy-specific интерпретация

Один и тот же Dispatcher context разные Strategy могут трактовать
противоположно.

Историческая пригодность конкретной Strategy для монеты (`StrategyCoinFit`)
относится к Analyst/research, а не к Dispatcher rating.

# 5. Качество

Каждый context обязан быть причинным, versioned, quality/freshness-aware и
иметь provenance. Missing/stale/partial не становятся neutral.
