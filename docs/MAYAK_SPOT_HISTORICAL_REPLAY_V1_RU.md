# MAYAK — exact historical Spot replay

**Версия:** 1.0 · 2026-09-06
**Статус:** implementation/research contract

## 1. Scope

Цель — причинно восстановить Spot executed flow для frozen ALL9/1063 через тот же `LiveMayakEngine`. `MAYAK_TRADING_EFFECT=NONE`; outcome/PnL не является входом replay.

## 2. Источник

Публичный архив Bybit `https://public.bybit.com/spot/<SYMBOL>/<SYMBOL>-YYYY-MM.csv.gz`. Используются фактические строки `timestamp, price, volume, side`; каждый source-файл сохраняется с SHA256.

## 3. Причинность

Для сигнала T движок получает только trades с `event_at <= T`. Для current-vs-prior 60m хранится минимум 120 минут pre-roll. Будущий trade не попадает в snapshot T.

## 4. Поля

Для Spot отдельно сохраняются окна 1/5/15/30/60m: buy/sell/net/turnover, net share, speed, acceleration, large buy/sell/share, prior turnover, turnover ratio и return. Эти поля не смешиваются с derivatives.

## 5. Research boundary

Entry outcome присоединяется только внешним Analyst после immutable Spot context. Никакой cutoff, gate, LONG/SHORT command или CoinMarketRating этим проходом не утверждается.

## 6. Instrument mapping

Версионированный source-adapter mapping: `1000PEPEUSDT` (linear contract) -> `PEPEUSDT` (Spot base asset). Spot notional всегда считается из фактических Spot `price × volume`; множитель имени derivatives-контракта не применяется к Spot. Mapping хранится в source manifest.
