# P45 — Multi-Touch Support/Resistance Zones

## Цель

Проверить на уже замороженном 90-дневном Entry V1 full panel, несёт ли дополнительную информацию классическая структура повторных тестов зон поддержки/сопротивления: первый/второй/третий/четвёртый+ тест, role reversal, ложные пробои, возраст зоны, сила предыдущего отбоя и степень сжатия цены возле зоны.

P45 — discovery-исследование. Оно не меняет Entry V1, Exit, Risk, live execution, leverage, stop-loss или take-profit.

## Данные

Период фиксирован: `2026-05-18 00:00 UTC .. 2026-08-16 00:00 UTC`.

Панель: UNI, LINK, BTC, ETH, XRP, 1000PEPE, SOL, DOGE, ADA.

Используются только локальные frozen-файлы:

- `trade_15m.csv` из P30 dataset;
- `p40/absorption_features.csv` как канонический список Core Entry exact-touch сигналов и исходов;
- P44 `regime_features.csv` и `calibration_thresholds.csv` — только как опциональный exploratory join для уже найденного BTC-relative residual.

Сеть не используется, рыночные данные не скачиваются.

## Каузальность / запрет look-ahead

Зона строится только из прошлого. Pivot имеет span `2 + 2`: центральный high/low становится подтверждённым только после закрытия двух последующих 15m свечей.

Для exact Entry `touch_at` детектор видит только 15m свечи, у которых `closed_at < touch_at`. Текущая незакрытая 15m свеча не используется.

## Замороженная геометрия

- Timeframe зон: 15m.
- Pivot span: 2 свечи слева + 2 справа.
- ATR: Wilder ATR(200), как в существующем Entry research.
- Полуширина зоны: 0.5 ATR.
- Близкие подтверждённые pivot-зоны с пересекающимися ATR-полосами объединяются в один физический уровень.
- Независимый новый retest учитывается только после отхода цены минимум на 1.0 ATR от зоны.
- Break/role-flip требует двух последовательных закрытий за дальней границей зоны.
- Один close за границей с возвратом до подтверждения считается false-break history.

## Что фиксируется на каждом Core Entry

- ближайшая aligned зона: LONG→support, SHORT→resistance;
- расстояние Entry до зоны в ATR;
- Entry внутри зоны или рядом;
- возраст зоны;
- число предыдущих независимых retest;
- ordinal текущего теста: first / second / third / fourth+;
- число pivot-подтверждений уровня;
- role reversal;
- false-break history;
- время от предыдущего retest;
- максимальный предыдущий rejection в ATR;
- доля последних 8 закрытых 15m свечей в пределах 1 ATR от зоны;
- slope расстояния к зоне за последние 8 свечей;
- range расстояния к зоне за последние 8 свечей.

## Предзаданные rule-cuts

P45 не перебирает произвольные параметры. До просмотра результатов фиксируются:

- inside aligned zone;
- distance <= 0.25 / 0.50 / 1.00 ATR;
- near 0.50 ATR + second+ / third+ / fourth+ test;
- near 0.50 ATR + multi-pivot;
- near 0.50 ATR + role reversal;
- near 0.50 ATR + false-break history;
- near 0.50 ATR + age <24h;
- near 0.50 ATR + age >=7d;
- near 0.50 ATR + previous rejection >=2 ATR;
- near 0.50 ATR + dense pressure/compression proxy.

P44 residual interactions помечаются как `p44_exploratory` и не могут считаться confirmatory результатом на этом же интервале.

## Метрики

Для каждого правила и актива:

- sample;
- `+0.5 before -1.0` all-signals rate;
- `+0.5/-1.0` decisive rate;
- `+1.0 before -1.0` all-signals rate;
- `+1.0/-1.0` decisive rate;
- uplift против Core baseline.

Отдельно строятся:

- 9-asset transfer matrix;
- LONG/SHORT matrix;
- S1/S2/S3 stability;
- first/second/third/fourth+ touch matrix;
- S1-calibrated quartiles continuous zone features → S2+S3 OOS transfer;
- descriptive zone catalog и 15m retest-event catalog.

## Методологический статус

P45 завершает discovery-фазу структуры Entry. Любой признак, который будет выбран после просмотра P45, должен быть заморожен до следующего нового временного holdout. Повторная оптимизация на этих же 90 днях не является подтверждением.
