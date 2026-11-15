# P53 — 1M Entry Displacement V1.2

## Задача

P53 отвечает на один узкий вопрос: **куда минутный таймфрейм тянет уже существующую frozen точку Entry V1**.

Исходные 1063 Entry не генерируются заново и не заменяются. Для каждого сохраняются исходные `symbol`, `direction`, `candidate_bar_at`, exact `touch_at` и `entry_price` из P40. Сначала скрипт обязан каузально восстановить исходную геометрию 15m+5m и получить ту же цену Entry. Если цена не воспроизводится, проход останавливается.

## Знак результата

Все LONG и SHORT приводятся к единой шкале:

- `shift < 0` — минутка тянет вход **глубже**: LONG ниже, SHORT выше;
- `shift = 0` — цена совпадает;
- `shift > 0` — минутка тянет вход **наружу / по направлению сделки**: LONG выше, SHORT ниже.

Это side-normalized displacement, а не PnL.

## Как добавляется 1m

P53 не подбирает новую формулу. Минутная зона получает тот же тип локальной геометрии, что frozen 5m zone:

- тот же bar-count lookback, что у frozen 5m;
- тот же ATR period в барах;
- тот же `zone_half_width_atr`;
- тот же shock ATR period / multiple;
- тот же post-shock maturity **в минутах**: 60 минут превращаются в 60 закрытых 1m баров;
- тот же `confluence_max_gap_percent` используется только как описательный strict 15m+5m+1m confluence check.

Почему bar-count переносится без умножения на 5: frozen 15m и 5m уже используют одинаковое число баров при разной длине одного бара. P53 продолжает именно эту архитектурную конвенцию и ничего не оптимизирует по результатам.

Для LONG гипотетический 1m Entry = `1m support_top`. Для SHORT = `1m resistance_bottom`.

## Два causal snapshot

1. `candidate` — только 1m свечи, полностью закрытые **до начала frozen 5m candidate bar**. Это статический контроль без дополнительной информации внутри текущей 5m свечи.
2. `pre_touch` — только 1m свечи, полностью закрытые **до минуты, в которой произошёл exact touch**. Это основной ответ P53: что минутная структура успела каузально показать прямо перед фактическим касанием.

Свеча, внутри которой произошёл exact touch, в 1m zone не входит.

## Источник 1m

Никаких Downloads. 1m OHLC строится только из frozen `dataset/public_trades/*.csv.gz`.

Fail-closed проверки:

- наличие всех архивов из `dataset_manifest.json`;
- SHA256 каждого raw archive при первоначальном построении cache;
- raw trade archive может законно не содержать сделок в отдельной минуте; такая **zero-trade minute** детерминированно представляется flat 1m bar по последней каузально известной цене (`O=H=L=C=previous close`, `volume=0`);
- leading zero-trade minute первого доступного UTC day без предыдущей каузально известной цены остаётся hard fail; внутри последующих дней seed берётся только из close предыдущего уже проверенного дня;
- после этого grid обязан содержать ровно 1440 последовательных минут на UTC day;
- строгая монотонность timestamp;
- trade-derived 1m OHLCV, агрегированный обратно в 5m, обязан **точно** воспроизвести frozen Bybit 5m OHLCV; это отделяет реальную минуту без сделок от повреждённого/неполного tape;
- frozen `trade_5m.csv` / `trade_15m.csv` обязаны совпасть с manifest fingerprints;
- frozen 15m+5m Entry price обязана воспроизвестись для каждого из 1063 сигналов.

При несовпадении исследование останавливается, а не подбирает другое правило.

## Cache / resume

1m cache хранится в output report directory по дням в gzip CSV. Cache валиден только при совпадении:

- P53 cache version;
- SHA256 frozen dataset manifest;
- archive SHA256 из manifest;
- SHA256 самого cache-файла;
- явной cache-семантики `zero_trade_fill=previous_close_flat_zero_volume`.

При первом построении raw archive дополнительно хэшируется и сверяется с manifest. Повторный запуск может переиспользовать уже доказанный cache без повторной декомпрессии многогигабайтного tape.

## Дополнительная проверка цены

Если `pre_touch shift < 0`, P53 отдельно проверяет raw public trades после исходного exact touch: была ли предложенная более глубокая цена реально доступна в следующие 3 часа.

Это **не** backtest новой стратегии и не разрешение менять Entry. Это только ответ на вопрос, была ли такая цена фактически дана рынком.

Для `shift > 0` обратный replay не объявляет более ранний fill: 1m уровень мог стать известен позже, поэтому такой сценарий требует отдельной динамической симуляции и в P53 не подменяется look-ahead.

## Outputs

- `entry_1m_displacement.csv` — machine truth по 1063 Entry;
- `summary.json` — aggregate / Long-Short / symbol статистика;
- `SUMMARY_RU.md` — краткое представление;
- `provenance.json` — fingerprints исходников и machine-truth outputs;
- `cache_1m/` — resumable локальный cache.

## Что P53 не меняет

- frozen Entry V1 fingerprint;
- P46 / NEW5 holdout;
- Exit;
- Risk;
- Execution;
- live runtime;
- UI;
- `reports/` во время установки патча.

P53 — research overlay. Любое изменение production Entry после результата требует отдельного решения и нового OOS.


## V1.1 preparation correction

V1.1 does not change the research contract or displacement formula. It corrects the call contract for the existing `flow_reversal_v1._archive_map(dataset_dir)` helper and adds a regression test for that one-argument API. P53 V1 was rejected by the fail-closed installer before installation, so no migration from an installed V1 state is required.


## V1.2 zero-trade minute correction

V1.1 ошибочно трактовал отсутствие сделок в любой минуте как отсутствие рыночных данных и требовал 1440 trade-bearing buckets. На реальном frozen UNIUSDT archive `2026-05-17` это дало 1413 минут со сделками и 27 законных минут без сделок, поэтому исследование остановилось до расчёта Entry displacement.

V1.2 исправляет именно этот класс ошибки подготовки research-кода:

- существующие frozen raw archives и их SHA256 не меняются;
- отсутствующий trade bucket внутри дня не «ремонтируется» будущей ценой: flat bar получает только последнюю уже известную цену;
- первый доступный день без causal seed всё ещё падает fail-closed;
- cache version повышена, поэтому несовместимый V1.1 cache не переиспользуется;
- equivalence gate усилен с OHLC до OHLCV;
- количество zero-trade minutes записывается в provenance/cache metadata.

Формула 15m+5m baseline, 1m zone, sign normalization, 3h availability, frozen 1063 cohort и запрет на NEW5/P46/live остаются без изменений.
