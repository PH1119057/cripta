# CRIPTA — единый исследовательский контур данных

**Версия:** 1.0
**Дата:** 2026-09-21
**Статус:** активный routed canonical data contract

Этот документ определяет единый физический и логический контур данных для
research / replay / backtest / OOS / holdout. Он не задаёт торговую Strategy
и не превращает исследовательский результат в trading policy.

# 1. Главное правило

Для исследования нельзя выводить период или universe из памяти, названия старого
run или случайно выбранного каталога.

Каждый research run до старта обязан явно зафиксировать:

```text
EXPECTED_UNIVERSE
ACTUAL_UNIVERSE
EVALUATION_PERIOD
WARMUP_PERIOD
REQUIRED_FIELDS
FIELD_COVERAGE
SOURCE_PROVENANCE
NO_DATA_INTERVALS
```

Если хотя бы одно обязательное поле не покрыто на части периода, это поле на
этой части имеет статус `NO_DATA`. Нельзя молча сокращать universe, менять
период, считать отсутствующее значение нулём или выдавать subset за full-universe.

# 2. Основной исследовательский universe

Текущий основной research universe проекта состоит из 20 linear USDT symbols:

```text
AAVEUSDT
ADAUSDT
APTUSDT
ARBUSDT
AVAXUSDT
BCHUSDT
BNBUSDT
BTCUSDT
DOTUSDT
ETHUSDT
HBARUSDT
INJUSDT
LINKUSDT
LTCUSDT
OPUSDT
SOLUSDT
SUIUSDT
TRXUSDT
UNIUSDT
XRPUSDT
```

Если владелец не задал иной universe явно, фразы «полный расчёт», «все монеты»,
«основной тест», «full-universe» для текущего research означают именно эти 20.

Старые панели 7/9/10/13 symbols разрешены только как:

- историческая воспроизводимость;
- paired comparison со старой Strategy / Entry;
- pilot / smoke;
- совместимый subset при отсутствии части данных.

Такой результат обязан быть подписан `SUBSET` и не может подаваться как
основной full-universe результат.

В частности, 10-symbol panel старого Entry V1 может использоваться для прямого
paired comparison, но основной новый research всё равно считается на 20 symbols.

# 3. Проверенная карта текущего raw-архива

## 3.1 Основной архив

Проверенный server source:

```text
/data/cripta/datasets/raw/20260518_20260816
```

Полный архив физически содержит 24 symbols. Помимо текущих 20 в нём также есть:

```text
1000PEPEUSDT
DOGEUSDT
NEARUSDT
XLMUSDT
```

Эти четыре symbols являются дополнительным historical evidence и не входят
автоматически в текущий основной universe.

По manifest архива:
- status = complete;
- public trades: исходный интервал 2026-05-17 .. 2026-08-15;
- orderbook depth 200: 2026-05-18 .. 2026-08-15;
- manifest содержит SHA256/size inventory.

## 3.2 Текущие 20 symbols: public trades

Для всех текущих 20 symbols проверены одинаковые непрерывные локальные интервалы
exact derivatives public trades в объединении current raw/cache sources:

```text
2026-05-17 .. 2026-08-15
2026-08-26 .. 2026-09-06
```

Локальный разрыв:

```text
2026-08-16 .. 2026-08-25
```

После 2026-09-06 локальное persistent coverage также не считается доказанным
только по этому архиву.

Крайние имена файлов без проверки непрерывности не являются доказательством
coverage.

## 3.3 Текущие 20 symbols: orderbook depth 200

Для всех текущих 20 symbols в основном архиве проверены 90 дневных файлов:

```text
2026-05-18 .. 2026-08-15
```

Это exact stored orderbook source для данного периода.

Расширение orderbook после 2026-08-15 не считается существующим, пока не
проверен отдельный source/manifest.

## 3.4 Размер текущих 20 symbols в основном архиве

Проверенный размер только текущих 20:

```text
public trades:  21.792 GiB
orderbook 200:   70.654 GiB
```

Полный historical archive со всеми 24 symbols занимает около 117 GiB.

Следствие: нельзя без preflight свободного места автоматически дублировать
весь raw archive или продолжать orderbook на месяцы вперёд на системном диске.

# 4. Что является exact raw, что можно восстановить, а что нельзя

## 4.1 Derivatives public trades

Статус:

```text
HISTORICALLY_RECOVERABLE=YES
```

Bybit публикует архивные public trades. Из них причинно восстанавливаются:
- exact trade time;
- price;
- size;
- taker side;
- trade id;
- производные свечи;
- исполненный derivatives buy/sell notional flow;
- causal price path;
- MFE/MAE и exact intrabar ordering для replay.

Derived money flow из public trades является производным от exact raw и должен
хранить provenance исходных trade ids / source files.

## 4.2 Open Interest

Статус:

```text
HISTORICALLY_RECOVERABLE=YES
NATIVE_MIN_INTERVAL=5m
```

Bybit REST `/v5/market/open-interest` поддерживает historical start/end,
pagination и интервалы от 5 минут.

Отсутствие OI внутри старого raw-каталога не означает, что OI нельзя
исторически backfill. Но до фактического backfill/manifest статус остаётся
`NO_DATA`.

## 4.3 Funding

Статус:

```text
HISTORICALLY_RECOVERABLE=YES
```

Bybit REST `/v5/market/funding/history` поддерживает historical funding rate.

До сохранения и проверки backfill конкретного периода funding остаётся
`NO_DATA` для этого research dataset.

## 4.4 Mark / index / premium

Статус:

```text
HISTORICALLY_RECOVERABLE=YES
```

Bybit предоставляет historical mark/index/premium price klines. Они могут
backfill отдельный positioning/context layer с exact provenance.

## 4.5 Exact liquidations

Статус:

```text
HISTORICALLY_RECOVERABLE_FROM_CURRENT_BYBIT_PUBLIC_ARCHIVE=NO
REALTIME_CAPTURE=YES
```

Bybit `allLiquidation.{symbol}` является realtime public WebSocket stream.
Публичный historical archive `public.bybit.com` не содержит отдельного
liquidation dataset, а public market REST history endpoint для exchange-wide
historical liquidation events не подтверждён.

Следовательно:

- exact liquidation event нельзя реконструировать из обычного public trade
  только по цене/size/side;
- отсутствие liquidation event в непокрытом прошлом = `NO_DATA`, не ноль;
- сторонний historical liquidation provider допустим только как отдельно
  утверждённый source с собственной provenance/coverage/version;
- proxy нельзя называть exact liquidation.

Текущий locally stored exact liquidation evidence начинается только там, где
наш collector действительно записывал `allLiquidation`.

Консервативно доказанный historical interval, используемый текущим research:

```text
2026-08-31T01:43:27.372Z
..
2026-09-18T09:53:47.890Z
```

После разрыва collector exact liquidations снова существуют с 2026-09-21, но
новый непрерывный coverage interval обязан подтверждаться отдельным coverage
manifest/liveness evidence. Сам факт первой новой liquidation строки не доказывает,
что предыдущие минуты были покрыты.

# 5. Почему старый MAYAK replay показывал деньги, но не liquidation/OI/funding

Старый historical MAYAK replay мог причинно рассчитывать derivatives money flow
из exact public trades. Это не означает наличие всех остальных market sources.

В старом replay source coverage встречается:

```text
derivatives_public_trades = EXACT_RAW_ARCHIVE
derivatives_price_path    = EXACT_RAW_ARCHIVE
funding                   = NO_DATA
open_interest             = NO_DATA
mark_index_premium        = NO_DATA
liquidations              = NO_DATA_EXACT_SOURCE_NOT_AVAILABLE
spot_public_trades        = NO_DATA
```

Поэтому запрещено формулировать:

> «раз мы посчитали движение денег по минутам, значит у нас есть все
> positioning/liquidation поля за тот же период».

У каждого физического поля собственный coverage contract.

# 6. Единый логический research contour

Основной подход проекта:

```text
verified raw segments
+ historically recoverable REST layers
+ realtime-only captured layers
↓
field-level coverage map
↓
neutral causal dataset
↓
Strategy/research passes
```

Единый research contour не требует хранить все источники в одном каталоге.

Разрешено хранить тяжёлый raw сегментами, если resolver/manifest однозначно
показывает:
- symbol;
- source type;
- start/end;
- contiguous intervals;
- gaps;
- checksum/provenance;
- quality.

# 7. План нормализации май → текущая дата

## 7.1 Обязательный baseline для 20 symbols

Нужно довести логический контур с 2026-05-17 до текущей даты:

1. public trades — закрыть локальные gaps и продолжить до current date;
2. OI 5m — historical REST backfill;
3. funding — historical REST backfill;
4. mark/index/premium — historical REST backfill по необходимости;
5. orderbook — использовать existing exact 2026-05-18..2026-08-15; дальнейшее
   расширение только после отдельного storage/source preflight;
6. liquidations — использовать только proven exact capture intervals; прошлое без
   источника сохранять `NO_DATA`.

## 7.2 Хранение

Не создавать второй полный дубль 117 GiB.

Предпочтительный путь:

```text
existing immutable raw archive
+ missing-day streaming downloads
+ compact neutral minute/5m derived dataset
+ checksums/source manifest
```

Скачанный missing-day raw может удаляться после materialization только если:
- checksum/provenance записаны;
- derived output проверен;
- исходный день можно воспроизводимо скачать заново;
- research contract не требует сохранения exact raw этого типа.

Для orderbook это решение принимается отдельно из-за объёма и необходимости
точного replay.

# 8. Обязательная шапка каждого research result

Каждый итоговый research report начинается минимум с:

```text
UNIVERSE_EXPECTED=<20 or explicit subset>
UNIVERSE_ACTUAL=<...>
UNIVERSE_STATUS=FULL|SUBSET|PARTIAL

EVALUATION_START=<...>
EVALUATION_END=<...>
WARMUP_START=<...>

PUBLIC_TRADES_COVERAGE=<intervals>
ORDERBOOK_COVERAGE=<intervals|NO_DATA>
OI_COVERAGE=<intervals|NO_DATA>
FUNDING_COVERAGE=<intervals|NO_DATA>
LIQUIDATION_COVERAGE=<intervals|NO_DATA>
OTHER_REQUIRED_FIELDS=<...>

STRATEGY_ID=<...>
STRATEGY_VERSION=<...>
ENTRY_PLAN_FINGERPRINT=<...>
EXIT_PLAN_FINGERPRINT=<...>
FEE_MODEL=<...>
```

Если `UNIVERSE_EXPECTED=20`, а фактически посчитано 5, 7, 10, 13 или 19:

```text
FULL_RESULT=NO
STATUS=PARTIAL
```

Частичный результат можно показывать как промежуточный, но нельзя выдавать за
исследование всего текущего universe.

# 9. Paired comparison и основной результат — разные отчёты

Если старый Entry/Strategy существовал только на subset, формируются два слоя:

```text
A. PRIMARY FULL-UNIVERSE RESULT
   текущие 20 symbols

B. PAIRED COMPARISON
   exact intersection old/new datasets/Strategy
```

Нельзя уменьшать A до размера B только ради удобства сравнения.

# 10. Holdout / OOS

Расширение historical dataset до текущей даты не превращает уже многократно
использованный период в новый holdout.

Для каждого периода отдельно хранится его роль:

```text
RESEARCH/TUNING
SAME-PERIOD COMPARISON
HOLDOUT/OOS
FORWARD/SHADOW
```

Если период использовался для выбора/подстройки параметров, он больше не
называется независимым holdout.

# 11. Запреты

Запрещено:

- помнить universe «примерно»;
- выбирать 10 symbols только потому, что старый baseline был на 10;
- склеивать интервалы через gap без явного `NO_DATA`;
- считать крайние даты файлов доказательством непрерывности;
- выдавать derived money flow за наличие OI/funding/liquidation;
- восстанавливать exact liquidation по обычным trades;
- автоматически наследовать старую Strategy из reused dataset;
- начинать тяжёлый full research без data coverage manifest.

# 12. Текущий storage finding

На момент проверки 2026-09-21 системный filesystem сообщает около 4.5 GiB
свободного места.

Поэтому постоянное расширение всех 20 symbols одновременно, особенно depth-200
orderbook, требует отдельного storage capacity decision.

Это operational finding, а не вечный лимит архитектуры. Перед новой массовой
загрузкой свободное место проверяется заново.
