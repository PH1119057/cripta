# ENTRY V1 — MULTI-ASSET VALIDATION PROTOCOL

## Статус

После UNIUSDT и независимой проверки LINKUSDT Entry V1 не перенастраивается. Следующий эксперимент расширяет поперечную выборку активов, сохраняя тот же календарный 90-дневный интервал.

## Фиксированный интервал

- evaluation start: `2026-05-18T00:00:00Z`
- evaluation end: `2026-08-16T00:00:00Z`
- latest complete raw-trade day: `2026-08-15`
- public trade warm-up archive также включает `2026-05-17`

## Портфель из 9 инструментов

Уже исследованы:

1. `UNIUSDT`
2. `LINKUSDT`

Новая независимая группа:

3. `BTCUSDT`
4. `ETHUSDT`
5. `XRPUSDT`
6. `PEPEUSDT`
7. `SOLUSDT`
8. `DOGEUSDT`
9. `ADAUSDT`

## Правило чистоты эксперимента

До завершения всех семи новых прогонов запрещено менять Entry V1 по результатам отдельной монеты.

Сначала для каждого актива применяется одна и та же цепочка P30 → P31 → P33 → P34 → P35 → P36 → P37 → P39 → P40. После этого сравниваются:

- частота исходных кандидатов;
- baseline `+0.5% before -1%`;
- эффект 60m pause;
- core count и core days;
- core `+0.5/-1`;
- core `+1/-1` и decisive `+1/-1`;
- LONG/SHORT отдельно;
- три 30-дневных сегмента;
- MAE хороших Entry;
- переносимость flow/OI/orderbook признаков;
- доля `neither` для `+1/-1`.

Цель не в том, чтобы все монеты повторили UNI. Цель — отделить общие свойства Entry от свойств конкретного инструмента и режима.

## P43 prefetch

`scripts/prefetch_multi_asset_90d_windows.ps1` заранее готовит тяжёлую часть данных для семи новых инструментов:

- 5m/15m/60m klines через frozen P30 dataset;
- raw public trade archives на точном окне;
- 1m taker-flow aggregation;
- 5m open interest;
- все доступные дневные historical orderbook archives на 90-дневном evaluation окне.

Orderbook сохраняется в общем `dataset/orderbook_cache`. Обновлённый P41 runner использует этот же cache и для P39, и для P40 с `KeepArchives`, поэтому один и тот же дневной архив не требуется скачивать дважды.

Prefetch поддерживает повторный запуск: готовые P30 dataset manifest и полные orderbook ZIP переиспользуются; `.part` для orderbook пытается продолжить загрузку через `curl.exe`.
