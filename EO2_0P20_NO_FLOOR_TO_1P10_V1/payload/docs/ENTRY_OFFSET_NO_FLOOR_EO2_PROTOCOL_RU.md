# EO2 — Entry −0.20% без +0.10 floor: +1.10 против −1.00

**Version:** EO2 V1  
**Research only. Downloads: DISABLED / fail-closed.**

## Цель

Проверить экономику ровно тех сделок, которые в завершённом EO1 получили фактический shifted fill на 0.20% глубже исходного Entry, если после fill убрать раннюю защиту `+0.10%` и дать сделке жить до первого из двух событий:

- `+1.10%` от фактического shifted fill;
- `−1.00%` от фактического shifted fill.

Искусственный 72-часовой выход не используется. Replay идёт причинно до первого target/stop trade tick либо до конца frozen-данных `2026-08-16T00:00:00Z`.

## Фиксированный источник

EO2 не ищет новые Entry и не пересчитывает fill. Источник — завершённый EO1.2:

- frozen signals: `1063`;
- scenario: `ADVERSE_0P20`;
- реально filled: `846`;
- EO1 event CSV SHA256: `91044aba6f3148e6599a5ce9a7a1414126d19a9cbed28983e19f753203b1d44f`.

Любое несовпадение источника — fail-closed.

## Контракт сделки

После EO1 shifted fill:

- initial stop: `−1.00%`;
- target: `+1.10%`;
- activation `+0.10%`: **DISABLED**;
- positive floor `+0.10%`: **DISABLED**;
- первый raw-trade tick, пересёкший stop или target, определяет outcome;
- если до frozen end не достигнут ни stop, ни target, сделка получает `data_end_open` и не включается в realised PnL.

## Экономика

Для прямой совместимости с EO1 используется та же иллюстративная модель:

- margin `$100`;
- leverage `10x`;
- notional `$1000`;
- round-trip cost reserve `0.10%` цены;
- target `+1.10% gross` → `+1.00% net` → `+$10`;
- stop `−1.00% gross` → `−1.10% net` → `−$11`.

Break-even win rate для `+$10 / −$11` = `52.38095%` resolved trades.

Это не точная модель Bybit fees/funding/slippage и не portfolio backtest.

## Выходные файлы

- `eo2_events.csv` — машинная истина по каждой из 846 сделок;
- `summary.json` — ALL9 экономика и длительности;
- `per_symbol.csv` — разбивка по монетам;
- `SUMMARY_RU.md` — читаемая сводка;
- `provenance.json` — hashes, source truth и версия.

## Длительность

Для target и stop считаются exact raw-tape durations и cumulative buckets:

`<=5m`, `<=15m`, `<=30m`, `<=1h`, `<=3h`, `<=6h`, `<=12h`, `<=24h`, `<=48h`, `<=72h`, `>72h`.

Для target также сохраняются median / p75 / p90 / p95 / max time-to-target.

## Ограничения

EO2 — signal replay. Он не моделирует:

- одну активную позицию на символ;
- конфликты перекрывающихся сигналов;
- конечный капитал и margin concurrency;
- portfolio chronology;
- реальные комиссии/funding/slippage.

После проверки signal economics отдельный portfolio replay требуется, если результат будет использоваться как торговая модель.

## Что не меняется

EO2 не меняет Entry fingerprint, P50/P51/P53, SE1/SE2, Exit, Risk, Execution, MAYAK, live runtime или UI. Исследование только читает frozen source report и локальный raw public-trade tape.
