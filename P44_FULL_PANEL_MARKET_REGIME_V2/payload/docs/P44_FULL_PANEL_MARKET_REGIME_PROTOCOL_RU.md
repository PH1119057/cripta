# P44 — Full Panel Market Regime V2: frozen protocol

## Статус

P44 — исследовательский слой после завершённой Entry V1 cross-asset панели. Он не меняет Entry V1, live execution, Risk или Exit.

## Цель

Проверить, объясняет ли состояние широкого рынка различия качества Entry между активами и падение качества в последовательных 30-дневных сегментах. Особое внимание: BTC/ETH direction, shock, ALT↔BTC coupling/beta/residual, decoupling и внутренняя breadth девяти замороженных инструментов.

## Frozen inputs

Период: `2026-05-18 00:00 UTC -> 2026-08-16 00:00 UTC`.

Панель: UNI, LINK, BTC, ETH, XRP, 1000PEPE, SOL, DOGE, ADA.

Используются только локальные файлы:

- `p30/dataset/trade_5m.csv` для 5m цен;
- `p40/absorption_features.csv` как frozen Core Entry sample и outcome labels.

Интернет не используется. Отсутствие любого required input = fail-closed.

## Causality

Для сигнала в `touch_at` разрешены только 5m свечи с `closed_at < touch_at`. Текущая незакрытая 5m свеча не используется.

## Features

- BTC directional return: 5m / 15m / 60m;
- BTC 5m shock z-score относительно предыдущих 6 часов 5m volatility;
- BTC 3h volatility;
- ETH directional return 15m / 60m;
- ETH minus BTC relative strength;
- ALT/BTC rolling correlation: 3h / 12h;
- ALT beta to BTC: 12h;
- directional residual ALT vs BTC beta: 15m / 60m;
- breadth и median return остальных 8 инструментов панели;
- отдельная breadth по alt basket без BTC/ETH;
- cross-sectional dispersion.

Внутренняя breadth не называется TOTAL3 и не подменяет BTC.D/TOTAL3/USDT.D.

## OOS design

Три последовательных блока по 30 дней:

- S1: calibration only;
- S2: OOS;
- S3: OOS;
- primary result: S2+S3 together.

Q25/Q50/Q75 каждого feature фиксируются только по S1 Core Entry timestamps конкретного актива. Outcome не участвует в выборе порога. S2/S3 не могут менять thresholds.

## Candidate veto families

Форма кандидатов фиксируется до просмотра P44 outcomes:

1. BTC 15m most adverse quartile;
2. BTC adverse + high ALT/BTC coupling;
3. BTC adverse + high coupling + no favorable residual;
4. BTC adverse + high coupling with decoupling override;
5. BTC adverse shock + high coupling with decoupling override;
6. internal panel breadth adverse;
7. BTC + ETH + alt breadth simultaneously adverse.

Отдельно экспортируется `decoupling_override_state`.

## Primary metrics

- bad entries blocked %;
- good entries wrongly blocked %;
- net discrimination = bad blocked - good blocked;
- veto precision among decisive blocked entries;
- remaining +0.5-before-1 rate and uplift;
- LONG/SHORT and S2/S3 stability through raw exported rows;
- cross-asset median/IQR and count positive/negative transfer.

Никакой кандидат не становится hard veto автоматически. Решение возможно только после OOS + cross-asset review.
