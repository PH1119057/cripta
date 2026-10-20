# CRIPTA — карта первичного охвата 30 индикаторов (2026-10-20)

**Назначение:** завершить сначала общий обзор всех Bybit-индикаторов, не подменяя первичный охват углублённым QC и не приступая к поиску торгового edge. Evidence-only, **НЕ КАНОН**, не стратегия/LIVE. Authority: `PH1119057/cripta:main`; состав сверён с `research/microstructure/INDICATOR_PROGRESS_2026-10-19.md` и `CHECKLIST_EXECUTION_2026-10-19.md` на `main=6ff437a7be7d3dac947ece7e822ab569f453bc0f`.

## Значение статусов

- `SEEN`: есть физический расчёт на исторических данных хотя бы для части пяти монет; **не** означает валидированную физическую сущность или §21 READY.
- `SOURCE_GAP`: проверенного источника/полноты данных пока нет; нельзя обозначать пропуски нулями.
- `PARTIAL`: первичный охват неполон, заблокированные маршруты **не** повторять.
- `DEFERRED`: владелец явно оставил Cross-Venue последним.
- `CONTRACT_OPEN`: рассчитана лишь proxy-величина или термин требует дополнительной физической идентификации; этот этап лишь регистрирует открытый вопрос.
- `RESEARCH_READY`: в этой карте **ни одному индикатору не присвоен**; паспорта §21.6 и целевые периоды — отдельный этап.

## Единый обзор

| № | Индикатор | Первичный охват | Что уже видно | Что препятствует корректной интерпретации / следующий узкий шаг |
|---|---|---|---|---|
| 1 | RVOL | SEEN 5/5 | Относительный минутный оборот | Версия v1 ошибалась на границе минуты APT; v2 проходит числовой QC, формальная временная доступность OPEN |
| 2 | Trade Rate | SEEN 5/5 | Число исполнений в минуту | Сохранять исправленную версию timestamp-floor отдельно |
| 3 | Trade Rate Acceleration | SEEN 5/5 | Изменение частоты исполнений | То же; не путать с ростом уникальных ордеров |
| 4 | Large Trade Share | SEEN 5/5 | Доля объёма крупных исполнений по prior60 | Историческое определение крупности и отдельный causal as-of |
| 5 | Buy/Sell Delta | SEEN 5/5 | Агрессивный signed notional | Timestamp-floor v2, exchange side semantics |
| 6 | CVD | SEEN 5/5 | Кумуляция signed notional за день | UTC-reset и причинность |
| 7 | Delta Acceleration | SEEN 5/5 | Изменение signed notional | Timestamp-floor v2, minute boundary |
| 8 | Trade Streaks | SEEN 5/5 | Последовательности групп timestamp+side | Такие группы не доказывают parent order |
| 9 | Queue Imbalance | SEEN 5/5 | Near-mid bid/ask дисбаланс стакана | Independent summary 5/5, full-row task agent unknown; не повторять |
| 10 | Multi-Level OFI | SEEN 5/5; CONTRACT_OPEN | TOP1/rank0 и top5 stock proxy | Price-identity versus rank shift — owner formula decision |
| 11 | Order Book Depth | SEEN 5/5 | ±5bp глубина стакана | Full-row direct independent replay пока неизвестен |
| 12 | Pulling Rate | SEEN 5/5; CONTRACT_OPEN | Исчезновение видимого объёма, чувствительность clock | Cancel ≠ исполнение; cross-feed order causality не доказана |
| 13 | Depth Recovery | SEEN 5/5; CONTRACT_OPEN | Exact-price size recovery episodes | 2 pending старше 60s на day-end; intraday job unknown, expiration vs censor |
| 14 | Price Impact | PARTIAL 4/5 May20 | Signed flow vs next-minute future label | Не настоящий marginal impact, ARB blocked; FUTURE не feature |
| 15 | Effort / Result | SEEN 5/5 | Минутный модуль хода цены на объём | Не отдельный impact; direct primary full-row открыта, создание reader заблокировано |
| 16 | Absorption | SEEN 5/5; CONTRACT_OPEN | Цена против signed flow | Не доказана пассивная absorption |
| 17 | Exhaustion | SEEN 5/5; CONTRACT_OPEN | Decay signed flow за 5m, next-5m label | Future outcome исключительно label |
| 18 | Short-Term Autocorrelation | PARTIAL 3/5 May20 | Автокорреляция коротких ценовых изменений | LTC/ARB upstream block — HARD_STOP |
| 19 | Range Compression | SEEN 5/5 | Сжатие диапазона; independent all-row hash | §21 и source event/observed/available-after |
| 20 | Local Extremes | PARTIAL 3/5 May20 | Локальные экстремумы цены | LTC/ARB upstream block — HARD_STOP |
| 21 | Sweep / Reclaim | SEEN 5/5; CONTRACT_OPEN | Пробой/reclaim экстремума по trade prints | Не orderbook liquidity sweep; same-timestamp ties |
| 22 | VWAP / Volume Profile | SEEN 5/5 | Объёмно-взвешенная цена/профиль; all-row hash | §21 и опубликованная доступность |
| 23 | OI Change | SOURCE_GAP | Known roots shallow inventory не нашла OI source | Проверить authoritative OI dataset coverage, не считать нулём |
| 24 | Liquidation Spikes | SOURCE_GAP | Частичная hourly inventory по всем пяти | Full-day completeness не установлена, blocked alternative interpreter |
| 25 | Spot/Perp Divergence | SOURCE_GAP | Historical Spot May20 source не подтверждён | OpenAI upstream execution block, 0/5, нет основания для divergence |
| 26 | Cross-Venue Lead–Lag | DEFERRED | Внешние биржи | LAST BY OWNER DECISION, не запускать сейчас |
| 27 | Inter-Trade Duration | SEEN 5/5 | Промежутки между записями fills | Не уникальные ордера; exact timestamp |
| 28 | Trade-Size Distribution | SEEN 5/5 | Распределение notional fills | Ex-post p95/p99 нельзя применять intraday как causal thresholds |
| 29 | Bid/Ask Spread | SEEN 5/5 | Best bid/ask spread | Full-row book audit agent unknown |
| 30 | Replenishment / Resiliency | PARTIAL 3/5 May20 | Top5 stock recovery proxy | INJ/ARB status unknown; не same-order resiliency |

## Закрытие именно первого этапа

По этому реестру **26 индикаторов** имеют May19/May20 исследовательское evidence на всей когорте или на подмножестве: №1–22, №27–30. Среди них 4 с неполным **вторым днём** (#14/#18/#20/#30), остальные представляют основной охват 5/5 по указанному scope. №23–25 имеют непроверенные/неполные источники, №26 отложен решением владельца. **Все 30 классифицированы на уровне карты**; это не 30 физически полностью исследованных индикаторов, не 150 паспортов и не доказательства predictive edge.

Для завершения overview достаточно иметь запись по каждому: источник и availability, возможный физический estimator или явный `NO_DATA`, примеры на пяти монетах либо точная причина пропуска, provenance и ограничения. Не требовать полных §21 паспортов для обзорной карты; не затягивать обзор случайными QC/bug investigations. Если маршрут запретили — записать `BLOCKED` и продолжать незаблокированные работы, без обхода. Четыре неполных May20 ряда не стирать: May19 pilot отдельно.

**После обзора:** поочерёдно углублять каждый physical estimator и только после измерительного контракта/QC делать §21 per-pair паспорта. Сравнение индикаторов с будущей ценой, оптимизация thresholds, OOS и after-fee trading analysis **позже**. Внешние биржи (#26) в последнюю очередь.

## Проверка ресурсов перед расчётами

На контрольной точке текущего запуска `notifications_check`: running=0, один ранее завершённый сторонний исследовательский job `job_57c0a31c54f1` status succeeded (не принадлежит текущей обзорной задаче). `uptime` load averages 1.50/2.51/2.54; `free -m` available=2563MB, 0 swap; `ps` показывает MAYAK ~66% CPU и Lifecycle Supervisor ~19%, плюс PostgreSQL. **Это только моментальный снимок; слот вычислений под новую тяжёлую задачу не предполагается свободным без следующей проверки.** Никакой серверный replay этим документом не запущен.

## NEXT

1. Первичный обзор по №23 OI, №24 liquidations, №25 Spot/perp обозначен `SOURCE_GAP`, а не `ZERO`. Разрешённая задача — только происхождение и наличие источника с подтверждённым доступом (не повторять заблокированные вызовы).
2. Зафиксировать термин и estimator варианты №10/#12/#13/#14/#16/#17/#21/#30 как `CONTRACT_OPEN`, без выдуманных канонических формул.
3. После этого переходить к глубокой паспортной фазе, сохраняя №26 последним.

