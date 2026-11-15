# P53 — 1M Entry Displacement V1

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
- 1440 минутных свечей на полный UTC day; пропуски не заполняются синтетически;
- строгая монотонность timestamp;
- trade-derived 1m OHLC, агрегированный обратно в 5m, обязан **точно** воспроизвести frozen Bybit 5m OHLC;
- frozen `trade_5m.csv` / `trade_15m.csv` обязаны совпасть с manifest fingerprints;
- frozen 15m+5m Entry price обязана воспроизвестись для каждого из 1063 сигналов.

При несовпадении исследование останавливается, а не подбирает другое правило.

## Cache / resume

1m cache хранится в output report directory по дням в gzip CSV. Cache валиден только при совпадении:

- P53 cache version;
- SHA256 frozen dataset manifest;
- archive SHA256 из manifest;
- SHA256 самого cache-файла.

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
