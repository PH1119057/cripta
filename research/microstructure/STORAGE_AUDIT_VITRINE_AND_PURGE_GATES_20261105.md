# Крипта — ревизия источников, витрины и отдельный допуск к очистке

**Дата ревизии:** 2026-11-05 UTC. **Тип:** research/storage forensic evidence, не архитектурный канон, не migration и не разрешение на DELETE.  
**Authority:** `PH1119057/cripta:main`, проверенный `REMOTE_HEAD=SOURCE_HEAD=738ff5151bd5f17d49bdc5e1be7f033f52d1a08f`.  
**Workstream:** Issue #454; Draft PR #455; отдельная research-ветка `research/compact-native-minute-pilot-20261105`.  
**Owner intent:** провести ревизию и подготовить необходимые витрины; **само удаление выполнять отдельно**, не мешая параллельным системным изменениям.

## 1. Доказанная инвентаризация

Диск `/data/cripta` — отдельный `/dev/sdb1`, 196 GiB, занято ~174 GiB, доступно ~14 GiB при проверке; `/` — отдельный `/dev/sda1`, 20 GiB, доступно ~1.1 GiB. Очистка Data-диска **не** очистит System-диск. `notifications_check` на момент этого прохода не показал активных SentinelX jobs; это **не доказательство отсутствия** других systemd/timer/cron workloads.

### A. Исторический external raw, май–август 2026

Источник: `/data/cripta/datasets/raw/20260518_20260816`, Bybit LINEAR public trades + OB200.

- 20 монет, **3 620** файлов, **99 263 363 490 байт = 92.446 GiB**;
- по каждой монете 90 архивов OB200 за `2026-05-18 .. 2026-08-15` и 91 архив сделок за `2026-05-17 .. 2026-08-15`;
- проверена непрерывность **имён/дат и метаданных файлов**, но это **не** проверка содержимого/побитового SHA каждого архива;
- полная machine-readable классификация по 20 символам / 40 source-группам: `research/microstructure/evidence/legacy_raw_storage_inventory_20261105.json`.

| Группа | Размер, GiB | Текущий статус |
|---|---:|---|
| Пять инструментов APT / INJ / DOT / LTC / ARB | 13.404 | **PROTECT:** прямые исследовательские потребители |
| BTC + ETH (в составе оставшихся 15) | 39.486 | **CANDIDATE ONLY:** существующие raw-потребители, пофайловый SHA reacquisition не выполнен |
| Остальные 13 символов | 39.556 | **CANDIDATE ONLY:** требуется матрица реальных потребителей и пофайловый replay/QC |
| Все 15 вне защищённой пятёрки | 79.042 | **Не разрешено удалять** до выполнения всех gates |

**Прямая техническая зависимость:** `p90l90_90d_warmup_v2_20261024/inputs` содержит **455 исправных symlink** на первичные public-trades архивы пяти монет (по 91/монету). Удаление целей делает исследовательские входы неработоспособными. Source-level читатели используют исходные тики и OB200. Open Issues #158, #227, #233, #218, #237 затрагивают original native-tick price/OB200 semantics; актуальный статус каждого потребителя должен проверяться отдельно. Для всего `src/scripts/research/operations` static scan дал 129 упоминаний исторического root, 105 public_trades и 32 `ob200.data.zip`: это **счётчик текстовых ссылок**, а не число активных consumers.

### B. Второй external raw, сентябрь–октябрь 2026

`/data/cripta/datasets/raw/bybit_public_trades_daily_v1`: **28** реально присутствующих `.csv.gz` (14 BTC + 14 ETH), **1.879 GiB**, даты `2026-09-18 .. 2026-10-01`; это **не дубликаты** майско-августовского архива по периодам. `archive_state.json` зафиксировал 280 первоначальных downloads, но на текущем path обнаружено только 28; причина изменения состава и обязательства оставшихся manifest entries **не установлены**, не интерпретировать как автоматический мусор.

### C. Уже существующие витрины

`/data/cripta/datasets/canonical/mayak_dispatcher_market_v1`: `COVERAGE.json` фиксирует 34 293 MAYAK наблюдения, 943 Dispatcher, 20 символов. По `SCHEMA.md` это **event-sampled** витрина по историческим сигналам, **не непрерывная** 1-minute market tape. Не считать полным заменителем raw.

`/data/cripta/research/runs/minute_chunk_v1_APTUSDT_20260518_7d_20261105`: **PASS** по 7 суткам, 10 080 минут, 241 920 независимых сравнений, 0 расхождений, 1 855 943 байта gz на входные 266 751 893 байта, пик 99 MiB. **Частичные объективные признаки**, а не все нужные physical features; exact native trade/OB200 sequence не сохраняется.

`research/microstructure/evidence/trx_20260518_20260524_public_source_reacquisition_sha256_20261105.json`: **14 / 14 remote-vs-local exact SHA256 PASS**, 110 497 121 байт за семь суток TRX: 7 public trade + 7 OB200, с повторной загрузкой потоком без raw-file write. Доказана сегодняшняя возможность восстановления именно этих файлов; **будущая доступность и migration потребителей не доказаны**.

### D. Другие data-disk каталоги: отдельные классы, не indiscriminate cleanup

| Path / группа | Измеренный ориентир | Статус ревизии |
|---|---:|---|
| `research/tmp` | **0.053 GiB**, обход завершён | Кандидаты после определения владельцев/живых job и uniqueness; малая выгода |
| `research/cache/test_gate_venv` | **не менее 0.86 GiB**, неполный обход | Возможно пересоздаваемая среда; сначала точный dependency/CI/active-writer контракт |
| `research/runs` | **не менее 2.335 GiB**, неполный обход | Immutable research/evidence; **сохранить**, пока нет per-run lineage/backup/restoration |
| `reports` (shared operational) | **не менее 3.206 GiB**, неполный обход, один subtree EACCES | Старые ZIP snapshots могут быть единственным recovery/evidence. **Сохранить до backup/restore gate**, никакого массового удаления |
| `datasets/external/cryptohftdata` | Неполный обход, размер не установлен | Исторические liquidation-object receipts; **сохранить**, `NO_DATA` не приравнивать нулю |
| `postgresql_cold`, `backups`, `script_archive`, production DB | Полный size/consumer audit не проводился | **Нельзя трогать без отдельного backup/rollback/owner и actor gate** |

Частичный размер **не является** верхней оценкой и не должен суммироваться в точный объём диска.

## 2. Витрины: повторно используем уже существующую архитектуру, не создаём второй owner

### M1 — Historical Market Minute Facts

Совместить historical cold-research slices с действующим каноническим зерном **MarketSource + InstrumentIdentity + observed_minute**, не смешивать SPOT/LINEAR или Bybit public source с Bybit KZ без exact identity evidence.

В `CURRENT_PROJECT_MAP §37.6/§37.14` описана уже реализованная в production `analytics.market_minutes` R8c и source-level/read-model parity **233/233 известных аналитических путей**, R9 проверена на дату checkpoint. **Текущий loaded runtime/DB state в рамках этой filesystem-ревизии не проверялся.** Историческое cold-research хранение не должно физически дублировать действующую online-витрину бессрочно.

Physical historical per-minute: UTC interval/available-after, exchange/source identity, observed trade price O/H/L/C (если доказано; без подмены native closed candles), volume/notional Buy/Sell, signed delta/CVD with exact reset, turnover, price path/range, b/a spread/depth and visible changes, source/quality/NO_DATA/STALE, formula versions, file SHA and parser/run IDs. Факторы с несовместимыми границами/окнами и отсутствующие OI/funding/liquidations маркировать `NO_DATA`/`UNKNOWN`, не нулём.

**Критичный gap текущего APT-пилота:** в нём записан close, но нет полной O/H/L/C цены из трейдов и ряда требуемых R8c/source-specific признаков; поэтому его нельзя массово размножить и назвать финальной витриной.

### M2 — Verified Closed Candle History

Единственный producer closed bars по ARCH §48.4 / OBS §7.5 и `analytics.closed_candles` (фактический текущий apply проверять отдельно). Exact source 5m/15m/... closed OHLCV с venue/instrument/timeframe/close identity и gap QC. `market_minutes` **не** выдавать за exchange-native OHLC и не подменять owner закрытых свечей. H3/H9/L5-3 geometry при необходимости вычисляется поверх подтверждённых закрытых данных с versioned algorithm.

### M3 — Exact Native Replay Source Index (не minute-only)

Сохранять per-(symbol,UTC day,kind) file size/SHA256/URL/ETag/publisher metadata/receipt semantics, полноту file coverage, test-restoration и immutable source scripts. Исходные public trades/OB200 для задач, где критичен native tick order, exact-price level updates и per-message chronology, читаются потоково **по требованию**, если доказано возможное точное восстановление. Ни один 20–30MB minute summary не является lossless tape для таких задач. Для незаменимых event-linked фактов — отдельный компактный native event evidence по exact study/run и исходный hash.

### M4 — Exact Liquidation / OI / External Coverage

Оригинальные liquidation receipts и event units/side/price/coverage — отдельно от `market_minutes`; наблюдение `mayak_v2.liquidations` в каноне не даёт права копировать событие в другой full JSONB. Точные OI/funding/mark/index и publisher/local available-after в отдельных source lanes. При неполных исторических API/объектных данных — `NO_DATA`/`UNKNOWN`; по RESEARCH §21 нет права заменить exact change OI произвольным 5m snapshot.

### M5 — Research evidence/indicator registry

Результат каждого эксперимента с exact frozen period, symbol, LONG/SHORT, indicator formula/unit/window/quality, baseline + OOS/leakage/fees, input provenance, independent QC и per-instrument RESEARCH-READY gate. Git-версионированный research catalog (INDEX §48, `operations/dashboard/instrument_research_catalog.json`) — единственный owner durable per-coin result; не создавать параллельное хранилище тех же выводов.

## 3. Следующий безопасный маршрут без удаления и без нагрузки сейчас

1. **Схема/consumer audit:** подтвердить у действующих consumers полные M1–M5 физические требования и зарегистрировать статусы по каждому символу/источнику. Точный список `research/*` и legacy code references не равен активным use cases.
2. **Верифицировать исторический M1/M2:** current R8c schema + historical source, сначала один новый символ × один день, потом семь суток; independent numeric/native source QC и cross-day continuity, при необходимости 30-дневные блоки. Никакого полного перепрогона 20 × 90 до capacity и source gates.
3. **Только на подходящих источниках:** последовательно build/readback/hash compact marts; не раскладывать ZIP/GZIP во временные полные копии. В каждую границу включить no-leak/receiver unknown quality.
4. **Source reacquisition:** per-file verified exact SHA for any proposed purge, restoration test and code compatibility, плюс гарантия нужного периода; при ненадёжной будущей доступности сохранить необратимые exact facts.
5. **Separate purge approval:** сформировать точный `(path,size,sha256,consumers,status,backups,restore,gates)` manifest, получить owner approval именно на delete list, повторно проверить активные jobs/actor/path ACL/current source. Выполнить адресно, затем df parity и independent replay QC. **No blanket rm/chmod/root workaround.**
6. **После** очистки исследовать 14 из 15 новых немемных кандидатов с отдельным period/eligibility/coverage gate; NEARUSDT остаётся под карантином §18 до отдельного owner decision. Old Strategy thresholds/history не считать новой политикой.

## 4. Решение о файлах по состоянию ревизии

- **SAFE TO DELETE NOW = 0 байт.** Владелец прямо поручил отделить выполнение удаления от инвентаризации.
- **RESTORABLE VERIFIED CANDIDATES:** 14 файлов TRX, 110 497 121 байт, SHA proof PASS — **не** cleared for deletion до consumer migration/owner-approved manifest.
- **POTENTIAL HIGH-VOLUME CANDIDATES:** 15 вне защищённой пятёрки = 79.042 GiB — exact files/required markets/readers/reacquisition guarantee NOT VERIFIED; **не** green.
- **KEEP / PROTECTED:** пять native-replay-symbols (13.404 GiB), archive research symlinks and lineage, historical liquidations, backups, cold DB evidence, production HOT/IRREPLACEABLE data and snapshots pending restore audit.

## 5. Текущие статусы

```text
INVENTORY_SYMBOLS                  = 20 / 20 PASS (metadata)
INVENTORY_FILES                    = 3620 / 3620 PASS (metadata; no per-file SHA)
APT_7DAY_PARTIAL_MINUTE_MART       = QC_PASS / NOT_FULL_REPLACEMENT
TRX_7DAY_REACQUISITION             = 14/14 SHA256 PASS / NO DELETE
M1_HISTORICAL_FULL_FEATURE_PARITY  = NOT CHECKED HERE
M2_CLOSED_CANDLE_RESTORE           = NOT CHECKED HERE
M3_NATIVE_REPLAY_CONSUMER_MIGRATION= NOT VERIFIED
M4_LIQUIDATION/OI_COVERAGE         = NOT VERIFIED
PRODUCTION_R8_LOADED_IDENTITY      = NOT CHECKED HERE (see dated MAP checkpoint)
RAW_PURGE_PERMISSION               = NO
RAW_PURGE_EXECUTED                 = NO
RECLAIMED_BYTES                    = 0
OTHER_SYSTEM_MUTATION              = NONE IN THIS WORKSTREAM
```
