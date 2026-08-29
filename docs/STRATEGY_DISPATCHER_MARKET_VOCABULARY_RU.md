# ДИСПЕТЧЕР СТРАТЕГИЙ — СПРАВОЧНИК РЫНОЧНОЙ СРЕДЫ

**Документ:** `STRATEGY_DISPATCHER_MARKET_VOCABULARY_RU.md`  
**Версия:** 1.0  
**Назначение:** канонический словарь независимых характеристик рынка, которыми профили стратегий описывают требуемую среду.

## 1. Правила словаря

Каждая характеристика должна:

1. описывать внешний рынок независимо от конкретной стратегии;
2. иметь стабильный идентификатор;
3. иметь понятное русское название;
4. иметь причинное значение;
5. явно показывать недоступность данных;
6. по возможности хранить физическое значение и категориальную интерпретацию;
7. не содержать торговой команды.

Недопустимо: `GOOD_FOR_ENTRY_V1`.

Допустимо: `market.volatility = HIGH`.

## 2. Группы словаря

### A. Качество данных
- `data.quality` — общая пригодность снимка: HIGH / MEDIUM / LOW / INSUFFICIENT.
- coverage — доля требуемых источников.
- freshness — актуальность.
- transport health — здоровье каналов.
- source agreement — согласие независимых источников.

### B. Цена и направленность
- `market.direction` — STRONG_UP / UP / NEUTRAL / DOWN / STRONG_DOWN.
- `market.direction_strength` — NONE / WEAK / MODERATE / STRONG / EXTREME.
- `market.direction_persistence` — VERY_LOW .. VERY_HIGH.
- `market.move_maturity` — EMERGING / DEVELOPING / MATURE / LATE / EXHAUSTING.
- направление по горизонтам 1/5/15/60 минут.
- `market.timeframe_alignment` — CONFLICTING / MIXED / PARTIAL / ALIGNED / STRONGLY_ALIGNED.

### C. Волатильность и режим
- `market.volatility` — VERY_LOW / LOW / NORMAL / HIGH / EXTREME.
- `market.volatility_trend` — FALLING_FAST / FALLING / STABLE / RISING / RISING_FAST.
- `market.chaos` — LOW / NORMAL / HIGH / EXTREME.
- `market.transition_speed` — STABLE / SLOW_CHANGE / TRANSITIONING / FAST_CHANGE / VIOLENT_CHANGE.
- `market.regime_stability` — VERY_LOW .. VERY_HIGH.

### D. Ширина, синхронность и корреляция
- `market.breadth` — STRONGLY_BULLISH / BULLISH / BALANCED / BEARISH / STRONGLY_BEARISH.
- `market.synchronization` — LOW / NORMAL / HIGH / EXTREME.
- корреляционный режим — DISPERSED / NORMAL / HIGH / SYSTEMIC.
- межактивная дисперсия — LOW / NORMAL / HIGH / EXTREME.

Синхронность направления и статистическая корреляция — разные величины.

### E. Боковик и сжатие
- `market.range_state` — NONE / FORMING / ESTABLISHED / MATURE / BREAKING.
- качество боковика — POOR / NOISY / NORMAL / CLEAN / VERY_CLEAN.
- compression — NONE / LOW / MODERATE / HIGH / EXTREME.
- range width, duration, stability, false-break frequency.

### F. Пробой и расширение
- `market.breakout_environment` — HOSTILE / WEAK / NEUTRAL / FAVORABLE / STRONG.
- breakout stage — NONE / PRESSURE_BUILDING / INITIAL_BREAK / CONFIRMING / ESTABLISHED / LATE / FAILING.
- expansion — NONE / STARTING / DEVELOPING / STRONG / EXTREME / EXHAUSTING.

### G. Исполненный денежный поток
- `money.pressure` — STRONG_BUY / BUY / BALANCED / SELL / STRONG_SELL.
- `money.pressure_acceleration` — REVERSING / WEAKENING / STABLE / STRENGTHENING / SURGING.
- `money.spot_pressure` — отдельно спот.
- `money.derivatives_pressure` — отдельно срочный рынок.
- `money.spot_derivatives_alignment` — STRONGLY_ALIGNED / ALIGNED / MIXED / DIVERGING / STRONGLY_DIVERGING.

Нужно хранить абсолютный поток и значение относительно собственной причинной нормы инструмента/панели.

### H. Позиционирование
- `positioning.oi_regime` — STRONG_CONTRACTION / CONTRACTING / STABLE / EXPANDING / STRONG_EXPANSION.
- `positioning.price_oi_state` — PRICE_UP_OI_UP / PRICE_UP_OI_DOWN / PRICE_DOWN_OI_UP / PRICE_DOWN_OI_DOWN / MIXED.
- funding state — STRONG_SHORT_BIAS / SHORT_BIAS / NORMAL / LONG_BIAS / STRONG_LONG_BIAS.
- positioning crowding — NONE / LOW / MODERATE / HIGH / EXTREME.
- crowding direction — LONG / SHORT / MIXED / UNKNOWN.

### I. Ликвидность и стакан
- `liquidity.quality` — POOR / THIN / NORMAL / DEEP / VERY_DEEP.
- `liquidity.trend` — WITHDRAWING_FAST / WITHDRAWING / STABLE / REPLENISHING / REPLENISHING_FAST.
- book pressure — STRONG_BID / BID / BALANCED / ASK / STRONG_ASK.
- book stability — UNSTABLE / FRAGILE / NORMAL / STABLE / VERY_STABLE.
- `liquidity.absorption` — NONE / WEAK_BUY / BUY / STRONG_BUY / WEAK_SELL / SELL / STRONG_SELL.
- pressure efficiency — HIGH / NORMAL / LOW / COLLAPSING.

### J. Ликвидации
- `liquidation.intensity` — NONE / LOW / NORMAL / HIGH / EXTREME.
- `liquidation.acceleration` — FALLING_FAST / FALLING / STABLE / RISING / SURGING.
- `liquidation.breadth` — LOCAL / LIMITED / BROAD / MARKET_WIDE / SYSTEMIC.
- `liquidation.phase` — NONE / TENSION_BUILDING / CASCADE / EXHAUSTION / RECOVERY / UNCERTAIN.
- направление ликвидаций LONG / SHORT / BOTH.
- абсолютный денежный объём и относительная аномальность.

### K. Истощение и продолжение
- `market.exhaustion` — NONE / WEAK / FORMING / STRONG / CONFIRMED.
- `market.continuation_environment` — HOSTILE / WEAK / NEUTRAL / FAVORABLE / STRONG.
- `market.bounce_environment` — HOSTILE / EARLY / FORMING / FAVORABLE / STRONG.
- среда возврата к среднему — HOSTILE / WEAK / NEUTRAL / FAVORABLE / STRONG.
- reversal maturity — NONE / EARLY_SIGNS / FORMING / CONFIRMING / ESTABLISHED / FAILED.
- reversal quality — WEAK / FRAGILE / NORMAL / STRONG / BROADLY_CONFIRMED.

`EXHAUSTION = STRONG` не означает гарантированный разворот.

### L. Трендовое качество
- trend quality — POOR / NOISY / NORMAL / CLEAN / VERY_CLEAN.
- pullback depth — SHALLOW / NORMAL / DEEP / EXTREME.
- counter-move frequency — LOW / NORMAL / HIGH / EXTREME.
- recovery quality — NONE / WEAK / FRAGILE / NORMAL / STRONG.

### M. BTC / ETH / альты
- `btc.state` — STRONG_UP / UP / NEUTRAL / DOWN / STRONG_DOWN.
- `eth.state` — аналогично.
- alt relative strength — MUCH_WEAKER / WEAKER / SIMILAR / STRONGER / MUCH_STRONGER.
- отдельно деньги, OI, ликвидации и ликвидность BTC/ETH при доступности.

### N. Межбиржевой слой
После подключения нескольких площадок:

- cross-venue alignment — STRONGLY_ALIGNED / ALIGNED / MIXED / DIVERGING / STRONGLY_DIVERGING.
- cross-venue lead — NONE / BYBIT / BINANCE / OKX / DEX / MULTIPLE / UNKNOWN.
- lead-lag strength — NONE / WEAK / MODERATE / STRONG.
- estimated lead milliseconds + confidence.

Лидерство нельзя определять без корректной синхронизации часов и receive-time.

### O. Ротация капитала
При достаточных источниках:

- NONE;
- BTC_TO_ALTS;
- ALTS_TO_BTC;
- CRYPTO_TO_STABLES;
- STABLES_TO_CRYPTO;
- SPOT_TO_DERIVATIVES;
- DERIVATIVES_TO_SPOT;
- MIXED;
- UNKNOWN.

Не выводить при недостаточном evidence.

### P. Макроэкономический и политический фон
- `event.context` — NONE / UPCOMING / IMMINENT / ACTIVE / POST_EVENT.
- `event.importance` — LOW / MEDIUM / HIGH / EXTREME.
- pre-event uncertainty — LOW / NORMAL / HIGH / EXTREME.
- post-event reaction — NONE / WEAK / MODERATE / STRONG / EXTREME.
- reaction alignment — MIXED / PARTIAL / BROAD / SYSTEMIC.

Событийный слой не определяет направление сделки.

### Q. Рыночная энергия и хрупкость
- market energy — VERY_LOW / LOW / NORMAL / HIGH / EXTREME.
- energy trend — FADING_FAST / FADING / STABLE / BUILDING / SURGING.
- market fragility — LOW / NORMAL / ELEVATED / HIGH / EXTREME.
- fragility direction — UPSIDE / DOWNSIDE / BOTH / NONE / UNKNOWN.

Хрупкость описывает потенциальную нестабильность до сильного движения.

## 3. Приоритет V1 в коде

Первая реализация должна содержать только достаточно стабильные и универсальные оси:

1. data.quality
2. market.direction
3. market.direction_strength
4. market.direction_persistence
5. market.move_maturity
6. market.volatility
7. market.volatility_trend
8. market.breadth
9. market.synchronization
10. market.range_state
11. market.breakout_environment
12. money.pressure
13. money.pressure_acceleration
14. money.spot_pressure
15. money.derivatives_pressure
16. money.spot_derivatives_alignment
17. positioning.oi_regime
18. positioning.price_oi_state
19. liquidity.quality
20. liquidity.trend
21. liquidity.absorption
22. liquidation.intensity
23. liquidation.acceleration
24. liquidation.breadth
25. liquidation.phase
26. market.exhaustion
27. market.continuation_environment
28. market.bounce_environment
29. market.chaos
30. market.transition_speed
31. market.regime_stability
32. btc.state
33. eth.state
34. market.timeframe_alignment
35. event.context
36. event.importance

Отсутствующие данные в V1 допустимы и должны быть явно помечены.

## 4. Расширение словаря

Новую характеристику добавлять только если ответ на вопрос «имеет ли это понятие смысл при полностью выключенной торговле?» — да.

Если характеристика существует только для конкретного Entry, она принадлежит стратегии, а не Диспетчеру.

## 5. Стабильные идентификаторы

После публикации идентификатор нельзя тихо переопределить. Изменение смысла требует нового идентификатора или новой версии словаря.
