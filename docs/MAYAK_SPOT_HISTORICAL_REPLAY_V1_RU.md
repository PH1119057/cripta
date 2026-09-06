# MAYAK — exact historical Spot replay

**Версия:** 1.0 · 2026-09-06
**Статус:** implementation/research contract

## 1. Scope

Цель — причинно восстановить Spot executed flow для frozen ALL9/1063 через тот же `LiveMayakEngine`. `MAYAK_TRADING_EFFECT=NONE`; outcome/PnL не является входом replay.

## 2. Источник

Публичный дневной архив Bybit `https://public.bybit.com/spot/<SYMBOL>/<SYMBOL>_YYYY-MM-DD.csv.gz`. Для каждой монеты скачиваются только UTC-дни, пересекающие причинные окна `[T-120m, T]` frozen сигналов. Используются фактические строки `timestamp, price, volume, side`; каждый source-файл сохраняется с SHA256. Месячный архив не используется основным replay-path, чтобы не подавать в движок исторические сделки вне причинно нужных окон.

## 3. Причинность

Для сигнала T движок получает только trades внутри объединения причинных окон `[T-120m, T]`; будущий trade не попадает в snapshot T. Разрывы между такими окнами допустимы: `LiveMayakEngine` использует time-based windows и не получает искусственных событий в пропущенных интервалах.

## 4. Поля

Для Spot отдельно сохраняются окна 1/5/15/30/60m: buy/sell/net/turnover, net share, speed, acceleration, large buy/sell/share, prior turnover, turnover ratio и return. Эти поля не смешиваются с derivatives.

## 5. Research boundary

Entry outcome присоединяется только внешним Analyst после immutable Spot context. Никакой cutoff, gate, LONG/SHORT command или CoinMarketRating этим проходом не утверждается.

## 6. Instrument mapping

Версионированный source-adapter mapping: `1000PEPEUSDT` (linear contract) -> `PEPEUSDT` (Spot base asset). Spot notional всегда считается из фактических Spot `price × volume`; множитель имени derivatives-контракта не применяется к Spot. Mapping хранится в source manifest.
