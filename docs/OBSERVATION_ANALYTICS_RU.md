# CRIPTA — наблюдение, контекст, мониторинг и аналитика

**Версия:** 2.2
**Дата:** 2026-11-07
**Статус:** активный канонический контракт наблюдательно-аналитического контура

Этот документ объединяет MAYAK, Dispatcher, Monitoring, Lifecycle Supervisor,
Position Supervisor, Analyst и Research. Ни один из этих компонентов не получает торговые права из-за
того, что наблюдает, классифицирует или анализирует рынок.

# 1. MAYAK — независимое наблюдение рынка

## 1.1 Назначение

MAYAK отвечает только на вопрос:

> Что объективно происходит во внешнем рынке?

Он работает независимо от наличия активных Strategy, сигналов, позиций и
результатов нашей торговли.

## 1.2 Разрешённые данные

MAYAK может причинно наблюдать и нормализовать:
- public trades и цену;
- spot/derivatives исполненный поток отдельно;
- объём;
- OI;
- funding/premium/mark/index;
- обычный стакан и его динамику;
- ликвидации;
- breadth/synchronization/divergence;
- данные нескольких CEX/DEX;
- on-chain;
- внешние макро/политические события, если источник утверждён;
- data quality/freshness/provenance.

Неизвестные данные фиксируются как неизвестные, а не как ноль.

## 1.3 Запреты MAYAK

MAYAK:
- не знает правила конкретной Strategy;
- не оценивает прибыльность нашей Strategy;
- не создаёт StrategySignal;
- не разрешает и не запрещает Entry;
- не выбирает LONG/SHORT;
- не меняет stop/Exit;
- не отправляет ExecutionRequest;
- не мутирует Exchange;
- не учится автоматически на PnL.

## 1.4 Причинность

Live и historical replay должны использовать одинаковый физический смысл
признаков.

Для состояния в момент T используются только данные, реально доступные к T.

Нет точного raw-источника — слой сохраняет `NO_DATA`, а не синтетическую
замену, если эквивалентность не доказана.

## 1.5 Многобиржевость

Архитектура MAYAK не привязана к Bybit.
Каждый источник подключается адаптером с exact provenance.

## 1.6 Выход MAYAK

Выход — versioned strategy-agnostic market facts/context с:
- event/observed/received times;
- source lineage;
- quality;
- freshness;
- coverage;
- version/fingerprint.

Интерпретация этих фактов за конкретную Strategy происходит downstream.

## 1.7 Strategy-parameterized geometry facts

MAYAK/наблюдательный контур может причинно материализовать Geometry state/timeline по exact GeometrySpec, полученному из Strategy layer. MAYAK не выбирает depth/ATR/width/gap/stabilization. Один exact fingerprint рассчитывается один раз на symbol и может переиспользоваться consumers; иной result-affecting parameter => иной fingerprint. Timeline хранит достаточные current/previous boundaries, component extrema, ATR, structural-change/stability timestamps и provenance для причинного replay. Торговая интерпретация остаётся только в EntryPlan/ExitPlan.

## 1.8 MarketRegime и persistent episode

MAYAK обязан различать мгновенное движение и устойчивый общерыночный режим.

Один короткий horizon не может быть единственным физическим смыслом
MarketRegime. Канонически MAYAK должен иметь causal multi-horizon evidence,
достаточный минимум для различения:

~~~text
INSTANT STATE
SHORT-HORIZON REGIME
PERSISTENT REGIME
~~~

Exact horizons/thresholds не являются global defaults и утверждаются только
после research/owner decision.

OWNER DECISION 2026-10-03: исследованный adaptive multi-window слой пока
сохраняется только как объективное evidence с
`UTILITY_NOT_CONFIRMED`. Для каждого symbol причинно сохраняются раздельные
`1w / 2w / 4w` trailing-window evidence по направлению движения и доступным
`60m / 120m / 180m` horizons. Эти окна не усредняются в один threshold,
не выбирается универсальный winner, и `C1/C2/C3` consensus не становится
канонической классификацией.

Research evidence 2026-10-03 подтверждает, что наличие long-horizon evidence
физически устойчивее short-only impulse в исследованном focus15, но полезность
этого слоя для Strategy, CoinMarketRating, Dispatcher suitability либо
автоматического MarketRegimeEpisode **не подтверждена**. Поэтому:
- evidence хранится с exact window/horizon/direction/provenance/quality;
- disagreement между `1w/2w/4w` не маскируется усреднением;
- исследованные debounce `0/1/2/3m` и adaptive `P25/P50/P75` не становятся
  policy/default;
- evidence не меняет Entry/Position/Exit и не создаёт trading rights;
- promotion в regime classification/episode policy требует нового
  `RESEARCH RESULT -> OWNER DECISION -> CANON`.

MarketRegime должен опираться на versioned набор объективных факторов, где
доступны: direction, breadth, synchronization, persistence/duration,
acceleration/deceleration, BTC/ETH anchors, data quality/coverage и
money/OI/liquidation/liquidity evidence без подмены missing -> neutral.

Устойчивое состояние материализуется как causal MarketRegimeEpisode с минимум:

~~~text
regime_id
started_at
last_confirmed_at
duration
direction
severity
breadth
synchronization
quality
evidence/provenance
~~~

Episode не является Strategy signal и не содержит LONG/SHORT command.

## 1.9 Continuity, quality и MarketObservationAlert

MAYAK является инфраструктурным источником наблюдения, поэтому continuity
само является измеряемой частью качества.

Для требуемой snapshot cadence должны быть доступны минимум:

~~~text
EXPECTED_SNAPSHOTS
ACTUAL_SNAPSHOTS
MISSING_SNAPSHOTS
MAX_GAP
COVERAGE_PCT
~~~

Пропущенная временная корзина не маскируется следующим snapshot.

Quality обязана позволять различить как минимум независимое доверие к price/
regime, money flow, positioning/OI/funding, liquidations, liquidity/order book
и composite context.

Если значимый объективный рыночный режим меняется либо деградирует качество
критичного observation source, наблюдательный контур обязан уметь создать
durable MarketObservationAlert.

Минимальные классы:

~~~text
REGIME_CHANGE
MARKET_WIDE_STRESS
SYNCHRONIZATION_SPIKE
LIQUIDATION_CASCADE
DATA_QUALITY_DEGRADATION
SOURCE_OUTAGE
~~~

Alert сообщает факт внешнего рынка/качества, имеет durable event/delivery/
retry/acknowledgement либо explicit escalation, но не является Entry/Exit
command и не создаёт trading rights MAYAK/Dispatcher. UI-only без durable
delivery не считается доставленным alert, если класс события настроен как
owner-notifiable.

# 2. DISPATCHER — объективный прикладной контекст

## 2.1 Назначение

Dispatcher находится между MAYAK и Strategy и публикует удобный
strategy-agnostic прикладной контекст.

Он не отвечает на вопрос:
> подходит ли рынок конкретной Strategy?

## 2.2 Основные классы данных

Dispatcher может публиковать:
1. глобальный контекст рынка;
2. контекст конкретного инструмента/монеты;
3. объективный `CoinMarketRating`, если формула отдельно утверждена;
4. состояние торговой ёмкости аккаунта.

Account capacity минимум различает:
- total/equity;
- used;
- reserved;
- free;
- available_for_new_trading;
- freshness;
- source exchange/account.

## 2.3 Границы Dispatcher

Dispatcher:
- не читает PnL Strategy как рыночный признак;
- не знает Entry formula;
- не создаёт Strategy profiles/suitability;
- не включает и не выключает Strategy;
- не выбирает LONG/SHORT;
- не создаёт StrategySignal;
- не резервирует средства по своей воле;
- не отправляет ордер;
- не закрывает позиции.

## 2.4 Strategy-specific интерпретация

Один и тот же Dispatcher context разные Strategy могут трактовать
противоположно.

Историческая пригодность конкретной Strategy для монеты (`StrategyCoinFit`)
относится к Analyst/research, а не к Dispatcher rating.

## 2.5 Качество

Каждый context обязан быть причинным, versioned, quality/freshness-aware и
иметь provenance.

Missing/stale/partial не становятся neutral.

## 2.6 Dispatcher обязан добавлять прикладную структуру, а не скрытую Strategy policy

Dispatcher не должен быть простым byte-for-byte transport MAYAK и одновременно
не имеет права возвращаться к Strategy suitability.

Его canonical работа — causal strategy-agnostic normalization объективной
среды в устойчивый прикладной context, включая там, где доступны: global
direction, stress/severity, regime_id/regime age/transition, breadth,
synchronization, money pressure, positioning/OI, liquidation state, liquidity,
BTC/ETH anchors и quality/coverage/freshness.

Это описание среды, не решение ALLOW/BLOCK, не GOOD_MATCH конкретной Strategy
и не выбор LONG/SHORT.

## 2.7 CoinMarketContext / CoinMarketRating

Для каждого инструмента Dispatcher обязан иметь структурированный
CoinMarketContext, сохраняющий раздельный физический смысл price, money,
liquidity, positioning, liquidations, relative strength и quality.

CoinMarketRating допускается только как strategy-agnostic объективная
характеристика состояния инструмента. Он не может использовать PnL, success
rate наших Entry или suitability конкретной Strategy.

До отдельного owner-approved research/formula один scalar rating не
придумывается. Вектор объективных dimensions предпочтительнее ложной единой
оценки. Текущий implementation status хранится в CURRENT_PROJECT_MAP.

## 2.8 Per-symbol self-normalized CoinMarketContext — OWNER DECISION 2026-10-15

Цель этого capability — описывать объективное текущее состояние **одного
конкретного symbol**, а не ранжировать монеты между собой и не выдавать
Strategy suitability.

Канонический инвариант нормализации:

~~~text
RAW FACT FOR SYMBOL
-> CAUSAL HISTORY OF THE SAME SYMBOL
-> SELF-NORMALIZED EVIDENCE
~~~

Если dimension переводится в percentile/z-score/relative stress band, baseline
строится только по собственной причинной истории того же `symbol`. Нельзя
нормализовать APT через BTC, LTC через ARB либо использовать одинаковый
абсолютный USD threshold для разных инструментов. Cross-symbol/BTC/ETH relative
strength может сохраняться отдельной objective dimension, но не является
self-baseline монеты.

CoinMarketContext сохраняет физические dimensions раздельно. Первая
implementation/research stage обязана поддержать, где exact source доступен:

- price return/amplitude минимум `1m/5m/15m/30m/60m`;
- LONG/SHORT liquidation notional/count раздельно минимум `1m/5m/15m`;
- liquidation intensity относительно собственного OI и derivatives turnover;
- liquidation velocity и acceleration по каждой стороне;
- OI change / clearance;
- causal price response на forced liquidation flow;
- spot/derivatives executed flow;
- liquidity/depth/spread evidence, если источник доступен;
- mark/index/funding derivative state;
- multi-venue confirmation, если exact approved feeds доступны;
- freshness/coverage/quality/provenance для каждой используемой dimension.

Знак/название liquidation side обязаны следовать exact source semantics.
До доказанного parser/source mapping нельзя переименовывать exchange `Buy/Sell`
в `long_liquidation/short_liquidation` по догадке.

Self-normalized evidence обязано хранить одновременно raw value и описание
baseline, достаточное для replay:

~~~text
symbol
dimension_id
raw_value
normalized_value / percentile [если рассчитано]
baseline_spec_id
baseline_window
sample_count
observed_at
quality
provenance
version/fingerprint
~~~

Точные baseline windows, percentile boundaries, weights и state thresholds не
становятся global defaults. `1d/7d/30d/90d`, `q90/q95/q97.5/q99` и подобные
наборы разрешены только как research grid до отдельного
`RESEARCH RESULT -> OWNER DECISION -> CANON`.

До такого решения Dispatcher публикует вектор objective dimensions /
`CoinSelfBaselineEvidence`; единый `CoinMarketRating` не вычисляется.
Наблюдательный deployment этого capability обязан сохранять
`trading_effect=NONE`.

Strategy может использовать этот context только через exact versioned
`Strategy Context Policy` по TRADING_CONTOUR. Автоматический Entry block/allow,
Position action или Exit action не возникает из MAYAK/Dispatcher. Promotion:

~~~text
OBSERVATION / REPLAY
-> RESEARCH / OOS
-> OWNER DECISION
-> NEW STRATEGY VERSION / CONTEXT POLICY
-> TEST / SHADOW
-> LIVE EQUIVALENCE
-> MICRO_LIVE
-> LIVE
~~~

Missing/stale/partial source не становится `0`, `NORMAL` или хорошим состоянием.
Если exact liquidation history отсутствует, status = `NO_DATA`; proxy может
существовать только как отдельно названный/versioned research object.

# 3. MONITORING — наблюдение и UI

## 3.1 Два разных смысла мониторинга

Нельзя смешивать:
1. сбор/наблюдение рыночных данных по широкому universe;
2. Strategy-specific мониторинг условий торговли.

## 3.2 Market/coin monitoring

Технический market monitor может наблюдать больше монет, чем торгует конкретная
Strategy.

Наличие symbol в monitor:
- не разрешает торговлю;
- не включает Strategy;
- не создаёт allocation;
- не является Entry condition.

## 3.3 Strategy Monitor

Strategy Monitor отображает состояние как минимум в координате:

```text
Strategy version × symbol × direction
```

Один symbol может иметь несколько строк разных Strategy с разными Entry и
состоянием.

Trading universe конкретной Strategy определяется только её StrategyCard.

## 3.4 UI/read-model

UI/read-model:
- показывает факты;
- позволяет владельцу управлять только разрешёнными control-сущностями;
- не содержит скрытую торговую policy;
- не пересчитывает Entry независимо от канонического runtime;
- неизвестное показывает как неизвестное;
- в operational monetary surfaces показывает **один итог после комиссий**;
  gross PnL и отдельные fee components остаются audit/internal evidence, но не
  дублируются в operator-facing money result;
- положительный monetary result = green, отрицательный = red, zero = neutral;
  MAE как adverse metric также отображается red, а не warning-orange.

Deployment contract presentation/read-model layer:
- presentation HTML/CSS и read-only Dashboard projections/aggregations могут
  обновляться независимо от trading runtime;
- такой deploy не требует disarm, Strategy deactivation или restart Entry/Exit/
  private runtime/observer/lifecycle services;
- если меняется read-only Dashboard backend, допустим restart **только**
  `cripta-dashboard.service`;
- auth, control/mutation endpoints, Strategy/Entry/Exit/Execution semantics,
  gates/permissions/LIVE-arm и decision-affecting code не входят в этот scope;
- status.json сервиса является только текущим liveness snapshot и не является
  durable журналом ошибок;
- caught Universal Entry observer exception, прерывающий observer epoch /
  Strategy evaluation, обязан до retry зафиксировать durable operational fault,
  canonical token которого определён в CRIPTA_GLOSSARY_RU*.md;
- следующий успешный status refresh/restart не имеет права молча удалить или
  resolve такой fault; Dashboard обязан показывать unresolved fault красным до
  explicit operator/owner resolution;
- OWNER DECISION 2026-10-15: каждый `UNIVERSAL_ENTRY_OBSERVER_RUNTIME_ERROR`,
  включая severity `ERROR`, показывать в существующей центральной панели
  Dashboard «События системы»; повторы одной exact signature агрегируются
  через occurrence_count, без требования отвечать на каждое сообщение;
- явное закрытие после устранения общей причины допускает групповую работу
  оператора, но не автоматическое снятие unresolved fault при успешном epoch;
  каждый закрытый exact fault сохраняет reason/operator/time audit;
- для ошибок наблюдателя нужен отдельный durable delivery path
  `DASHBOARD_INBOX` в той же центральной панели, без обязательного клика
  оператора на каждое повторение; `OWNER_WEBHOOK` остаётся отдельным каналом,
  его условия доставки и получатели не меняются;
- запись в журнал, выдача через API и отображение в браузере — разные
  состояния. `DELIVERED` для `DASHBOARD_INBOX` допустимо только после
  подтверждения браузером фактического отображения уведомления, не от одного
  read-model/API fetch; отсутствие такого подтверждения сохраняет pending
  и требует retry/escalation. Успешный показ не закрывает underlying fault;
- повтор одного exact fault обновляет счётчик/последнее время и не порождает
  поток идентичных карточек; подтверждение показа не означает, что оператор
  устранил причину или согласился на resolution;
- migration/worker/read-model/client должны быть введены одним тестируемым
  release change; до этого `DASHBOARD_INBOX` не считается implemented,
  а показ старого banner не считается доказательством durable delivery;
- OWNER DECISION 2026-10-15: совместимый Observer/runtime и отдельно
  идентифицированный Dashboard bundle выпускаются согласованно из одного
  проверенного Git commit; до DB/schema cutover проверяются exact UI bytes,
  затем переключаются runtime и Dashboard identities до старта managed services.
  Частичная смена одного слоя без другого не считается успешным release;
  rollback после миграций требует отдельной проверки и не подразумевается
  автоматически при неудаче installer;
- exact Dashboard presentation/read-model bytes имеют отдельную
  `DASHBOARD_UI_COMMIT` identity.

## 3.5 Lifecycle Supervisor

Lifecycle Supervisor — технический сквозной контролёр lifecycle. Он не является
торговым слоем и не владеет trading policy.

Единственная каноническая lifecycle-chain находится в
docs/CRIPTA_ARCHITECTURE_RULES_RU_*.md §9.1. Этот документ её не дублирует.

Supervisor обязан видеть exact lineage/IDs и выявлять нарушение обязательных
handoff/invariants, включая:
- CAPITAL_RESERVATION_STUCK;
- EXCHANGE_POSITION_OWNERSHIP_INVARIANT_BROKEN;
- EXCHANGE_POSITION_MODE_MISMATCH;
- POSITION_WITHOUT_EXIT_OWNER;
- POSITION_WITHOUT_CONFIRMED_INITIAL_PROTECTION;
- потерянный/неподтверждённый required handoff/reconciliation.

Owner decision 2026-10-11 — exchange protection и Exit Engine ownership являются
**разными независимыми состояниями**, не заменяют друг друга:
- фактическая позиция, принятые биржей TP и Hard Stop подтверждаются
  read-only Exchange/account evidence для exact account + symbol + positionIdx;
  отсутствие свежего account evidence = UNKNOWN, не «защищена»;
- Exit Engine claim, last_seen heartbeat, active consumer и exact ExitPlan
  подтверждаются собственными operational evidence, независимо от TP/SL;
- потеря подтверждённого сопровождения открытой StrategyPosition остаётся
  CRITICAL / POSITION_WITHOUT_EXIT_OWNER и блокирует **новые** Entry по
  operational-safety contract даже при биржевом TP/SL;
- биржевой TP/SL не даёт права считать Exit Engine работоспособным, а healthy
  heartbeat не доказывает наличия актуального TP/SL на бирже;
- Dashboard обязан дать устойчивый красный приоритетный сигнал о потере
  сопровождения (без прокрутки консоли), exact position_id/symbol, причину,
  observed_at и recoverability; отдельное состояние биржевой защиты:
  VERIFIED / NOT_VERIFIED / UNKNOWN. UI refresh не очищает durable fault;
- repeat incidents группируются для восприятия, но все исходные
  события/отказы EntryDecision остаются доступными для аудита;
- восстановление требует доказанного active exact claim / heartbeat
  и штатного reconciliation. Нет обхода критического Entry gate,
  тихого auto-resolve, auto-close или изменения Strategy TP/Hard Stop.

Owner decision 2026-10-12 — временная credential model для independent
Supervisor Exchange verification:
- до отдельного security release разрешено использовать существующий
  approved Bybit account credential для **read-only REST calls** Supervisor;
  это не передаёт Supervisor владение торговыми мутациями;
- Supervisor code path не импортирует торговые mutation functions и не
  вызывает create/amend/cancel/close endpoints; credential доступ ограничен
  точным service principal и systemd credential delivery;
- independent account evidence обязательно свежие position/TP/SL и exact
  account/symbol/positionIdx; PostgreSQL и WS projection не могут выступать
  единственным доказательством VERIFY;
- проверяются оба фактически действующих TP и SL относительно current
  approved protection state/ExitPlan, а не только начальных Entry prices:
  legitimate Exit changes не должны ошибочно выглядеть как breach;
- отсутствие, устаревание и ошибка чтения = UNKNOWN (not VERIFIED);
  неверная фактическая защита = NOT_VERIFIED с durable fault/evidence;
- **TECHNICAL_DEBT / SECURITY**: разделить Exchange credentials на
  trading-mutation и least-privilege read-only, затем удалить использование
  общего trading credential из Supervisor; не считать временную модель
  permanent approved least-privilege architecture;
- это решение не подтверждает implementation/deploy/runtime; для включения
  независимого контроля нужны тесты read-only isolation, точных цен/rounding,
  dynamic TP/SL, failed/stale reads, actor-path/systemd permissions и
  release verification. LIVE re-arm остаётся отдельной owner operation.

EXCHANGE_POSITION_OWNERSHIP_CONFLICT не является critical lifecycle fault:
это штатный fail-closed EntryDecision outcome до real request.

Точные определения tokens принадлежат docs/CRIPTA_GLOSSARY_RU*.md.

Lifecycle Supervisor не имеет права лечить faults торговой догадкой.
Automatic protection reassert/reduce-only close допустим только если exact
Strategy version заранее содержит соответствующую owner-approved emergency/
protection-failure policy.

Critical fault обязан доходить до владельца через durable alert delivery:
событие/очередь, retry, acknowledgement либо explicit escalation state.
Отображение только в UI не считается доставкой. Потеря critical alert delivery
сама фиксируется как operational fault.

Lifecycle Supervisor:
- не создаёт StrategySignal;
- не создаёт EntryDecision/ExitDecision;
- не меняет stop/TP/trailing;
- не закрывает позицию по собственной оценке;
- не выбирает Strategy.

## 3.6 Position Supervisor

Position Supervisor наблюдает конкретную фактически открытую StrategyPosition.

Он может сохранять:
- current price;
- MFE/MAE;
- protection;
- current H9/H3/other Strategy geometry snapshots;
- смещения;
- current MAYAK/Dispatcher context.

Position Supervisor не получает право самостоятельно изменить Exit policy.

Он отвечает на вопрос «что фактически происходит с этой позицией сейчас?»,
тогда как Lifecycle Supervisor отвечает «правильно ли эта сущность проходит
обязательный сквозной lifecycle?».

# 4. ANALYTICS / RESEARCH — доказательный контур

## 4.1 Назначение

Analyst/research объясняет, что произошло, и создаёт доказательный материал.

Он не торгует и не меняет Strategy автоматически.

## 4.2 Единица анализа

```text
StrategyActivation / exact Strategy version
-> EntryPlan + ExitPlan fingerprints
-> causal market refs
-> signal_id (одна Strategy)
-> strategy_attempt_id
-> EntryDecision
-> optional EntryExecutionRequest/order/fill
-> optional StrategyPosition
-> optional ExitDecision
-> optional ExitExecutionRequest/close/protection fill
-> actual economics
```

Две Strategy в один момент имеют два независимых `signal_id`.

## 4.3 Причинность

При анализе состояния в T используются только данные, доступные к T.
Будущие данные используются только как outcome.

Связь по `symbol + ближайшее время` не заменяет exact IDs/source refs, если
точная связь должна существовать.

## 4.4 Observed vs Consumed

Различать:
- `OBSERVED_CONTEXT` — существовавший причинный контекст;
- `CONSUMED_CONTEXT` — контекст, реально использованный EntryPlan/ExitPlan.

## 4.5 Исследование никогда не канон

Любое исследование — старое, новое, OOS, holdout, replay, pilot, full-universe
или завершённое сегодня — остаётся доказательным материалом до отдельного
решения владельца.

Новое исследование не обязано наследовать постановку предыдущего исследования.

Если владелец ставит новый вопрос, исполнитель строит методику от:
1. текущего активного канона;
2. канонического словаря;
3. текущих доступных данных;
4. буквального нового вопроса.

Запрещено рассуждать:
> раньше Entry V1 делал так, поэтому и сейчас начнём с этого

если владелец явно не попросил сравнить с Entry V1.

## 4.6 Переиспользование старых данных

Большой накопленный dataset можно быстро переиспользовать, если доказано, что
его физические поля совместимы с новым вопросом.

Переиспользование данных не означает наследование старых thresholds, старой
Strategy, старого Entry, старых названий или старых выводов.

## 4.7 Новое исследование

До массового расчёта требуется:
- однозначно определить термины;
- зафиксировать объект/границы/единицы;
- проверить причинность;
- выполнить малый сквозной тест;
- проверить физически правдоподобный пример;
- только затем считать полную базу.

Если пользовательская формулировка содержит термин вне словаря — сначала
уточнение, а не догадка.

## 4.8 Геометрические исследования

Запрещён неоднозначный показатель вроде `h3_edge`.

Нужно явно указывать физический объект, например:
- lower_boundary_inner_edge;
- upper_boundary_inner_edge;
- entry_relative_near_edge;
- entry_relative_opposite_edge;
- working_range_width;
- exact depth/timeframe.

При повторных H9-touch research различает raw attempt/touch, exact candidate_id и причинный H9 episode; повторные касания одной устойчивой геометрии не выдаются автоматически за независимые рыночные возможности. Для текущего H3 research сохраняется H3/H9 timeline, directional distance от фиксированного H9 Entry до противоположной combined inner H3 boundary, переходы исследуемых порогов, возврат/невозврат к H9 Entry и последующие MFE/MAE/outcomes. H3<1%/H3>=1% — research classes, не канонический threshold.

## 4.9 Counterfactual / псевдосделки

Если Entry condition выполнился, но real Entry не состоялся по одной из явно
разрешённых admission-причин, Analyst может вести отдельную counterfactual
псевдосделку.

Разрешённые причины минимум:
- INSUFFICIENT_AVAILABLE_FUNDS;
- EXCHANGE_POSITION_OWNERSHIP_CONFLICT.

Counterfactual обязан сохранять exact Strategy/EntryPlan/ExitPlan lineage,
strategy_attempt_id, EntryDecision и counterfactual_block_reason.

Причины не смешиваются: capital shortage и slot conflict остаются разными
аналитическими классами.

Counterfactual:
- не резервирует капитал;
- не получает physical slot claim;
- не создаёт ExecutionRequest;
- не имеет Exchange mutation rights.

STALE_OR_UNKNOWN_REQUIRED_STATE и operational faults автоматически не
превращаются в полноценную псевдосделку, если causal replay нельзя доказать.

Counterfactual outcome всегда отделяется от фактического PnL.

## 4.10 Экономика

Торговые отчёты пользователю формулировать по-русски и различать:
- до комиссий;
- комиссии;
- после комиссий;
- funding;
- slippage;
- фактический PnL;
- counterfactual.

## 4.11 Из исследования в live

```text
ДОКАЗАТЕЛЬСТВА
-> РЕШЕНИЕ ВЛАДЕЛЬЦА
-> НОВАЯ STRATEGY / ВЕРСИЯ ДОКУМЕНТА
-> TEST/SHADOW
-> LIVE EQUIVALENCE
-> MICRO_LIVE
-> LIVE
```

Точные обязательные условия real arm определены в
docs/TRADING_CONTOUR_RU*.md §4.7. Ни liveness сервиса, ни DEPLOY, ни зелёные
unit tests сами по себе не дают LIVE rights.

## 4.12 Observation Replay Contour

OWNER DECISION 2026-10-03: modern exact replay для наблюдательного контура
означает полный causal strategy-agnostic pipeline:

~~~text
historical market events
-> MAYAK
-> quality / continuity
-> Dispatcher V2
-> MarketObservationAlert stage
~~~

Обязательные свойства:
- historical event ordering причинный; event/snapshot time не движется назад;
- MAYAK replay использует тот же `LiveMayakEngine`, а не копию feature formulas;
- Dispatcher replay использует те же production `dispatcher_v2` builders,
  config fingerprints, freshness/coverage/data-quality semantics;
- missing/stale/partial сохраняются как quality states и не становятся neutral;
- provenance сохраняет source lineage и `trading_command=false`;
- replay output не содержит Strategy suitability, CoinMarketRating formula,
  StrategySignal, Entry/Exit/Execution command;
- trading/account capacity не обязана входить в market replay и может
  добавляться только отдельным causal technical input;
- alert stage присутствует всегда.

До отдельной owner-approved alert generation policy alert stage имеет exact
статус `NO_POLICY`. В таком состоянии replay не генерирует
`REGIME_CHANGE`, `MARKET_WIDE_STRESS`, `SYNCHRONIZATION_SPIKE`,
`LIQUIDATION_CASCADE`, `DATA_QUALITY_DEGRADATION` или `SOURCE_OUTAGE` по
неутверждённым thresholds. Он может причинно воспроизвести явно существующий
MarketObservationAlert fact с его exact class/payload/provenance.

Observation Replay Contour заканчивается до Strategy и не является
LIVE EQUIVALENCE торгового контура сам по себе.

# 5. Общая граница наблюдательного контура

MAYAK, Dispatcher, Monitoring, Lifecycle Supervisor, Position Supervisor и
Analyst могут расширять видимость системы, но не становятся владельцами торговой policy.

Наблюдение, классификация, рейтинг, корреляция и статистическая полезность сами
по себе не создают право открыть, закрыть или изменить позицию.


# 6. Operator-visible critical state delivery

OWNER DECISION 2026-10-07: Dashboard sound delivery must distinguish routine
execution noise from operator-relevant safety transitions.

When Dashboard sound is enabled, exactly one alarm event is queued for a single
poll cycle if either condition becomes newly true:

- global mainnet new-Entry gate transitions `OPEN -> OFF`;
- a previously unseen open `CRITICAL` row appears in
  `runtime.lifecycle_faults`.

The alarm applies regardless of the currently selected trading subpage. Seen
critical fault IDs and the previous gate state are kept in browser-local state
only to suppress repeated sound for the same unchanged incident; PostgreSQL
remains the durable fault/state authority.

Routine PostOnly Entry order acknowledgement, waiting for fill, ordinary
zero-fill cancellation and other Bybit Entry-attempt noise belong to the
dedicated Bybit log/read-model and must not masquerade as an opened trade or a
critical audible alarm.

Browser audio policy still applies: if audio playback has not yet been unlocked
by a user gesture, the existing Dashboard sound queue holds the alarm until
playback becomes available.

# 7. Market-source vs trading-account identity — OWNER DECISION 2026-10-16

ARCH §49 owns Venue / MarketSource / TradingAccount identity. MAYAK, Dispatcher
and Analytics must preserve this separation.

## 7.1 Public market observation grain

Public/objective market facts are keyed causally by:

~~~text
market_source_id
instrument_id
observed/event time
source-specific event identity where applicable
~~~

venue_id is preserved directly or through immutable MarketSource lineage.
trading_account_id is not part of the public market-data grain.

Therefore N subaccounts on one Venue do not create N copies of price, public
trades, OI, funding, liquidations, public order book or CoinMarketContext when
the underlying MarketSource is the same.

## 7.2 Private account observation grain

Account/trading-capacity observations are a separate contour keyed by exact
trading_account_id and AccountConnector provenance. Wallet, free balance,
margin, private positions/orders, account mode and account health must not be
published as if they were market facts.

Dispatcher may consume both:
- strategy-agnostic market context from MarketSource;
- separate TradingAccount capacity/admission evidence.

These inputs remain distinct in provenance and retention. Trading-account
capacity is not duplicated into long-horizon market replay merely to accompany
each market minute.

## 7.3 Multi-venue and DEX observation

Different Venues keep distinct MarketSource facts even when their prices track
closely. CrossVenueMarketContext, venue spread, lead/lag, divergence, basis or
other cross-venue aggregates are derived contexts and must preserve contributor
IDs/freshness/quality.

For DEX/on-chain observation the market source may identify chain/network,
protocol/pool and block/event provenance instead of a centralized Exchange
feed. The same separation remains: public pool/chain state is market evidence;
the CRIPTA wallet used to transact is TradingAccount/private execution identity.



## 7.4 Compact analytical history

The compact analytical representation from ARCH §48 uses
MarketSource + InstrumentIdentity + time as its market grain. Strategy/account
labels are joined only when the research question requires actual
decision/execution/economics lineage; they must not cause physical duplication
of identical public market rows.

This section grants no trading command or Strategy policy.

## 7.5 Единственный общий producer закрытых свечей (ARCH §48.4)

Канонический MarketSource-адаптер наблюдательного контура загружает, проверяет и
материализует точные подтверждённые OHLCV 5m/15m и иные разрешённые интервалы
в единственном `analytics.closed_candles`, независимо от StrategyActivation.
Для startup/restart он **сначала** проверяет уже доступную историю в
production PostgreSQL по manifest exact потребителя, затем дозагружает только
недостающие closed candles из exact approved MarketSource через bounded Bybit
Public REST, без принудительного ожидания формирования полного live-окна. Ключ `MarketSource +
InstrumentIdentity + timeframe + UTC close`. MAYAK/Dispatcher не создают
отдельных физических копий тех же свечей; `analytics.market_minutes` остаётся
самостоятельным **производным** compact-minute dataset и не выдаётся за OHLC.
Наблюдательный контур отвечает за source/freshness/coverage/provenance.
Strategy/Entry/Exit отвечают только за свои вычисленные state/checkpoints.
Не создавать новый live cursor из исторических OHLC: trade continuity
проверяется отдельно по ARCH §48.4.


### 7.5.1 Ожидаемая подготовка Observer — OWNER DECISION 2026-11-04

Ожидаемая догрузка подтверждённой истории при холодном запуске является
`OBSERVER_PREPARING`, а не `UNIVERSAL_ENTRY_OBSERVER_RUNTIME_ERROR`.
Проекция: `state=PREPARING`, `observer_ready=false`, «Подготовка данных ·
ожидайте / NOT READY», конкретные symbol/timeframe/expected/actual и deadline.
Штатный этап не создаёт ERROR incident и не требует закрытия аварии владельцем.
Это узкий типизированный preparation outcome, не blanket suppression exceptions.

Все символы, которые действительно участвуют в **одном exact атомарном
Strategy/EntryPlan/consumer scope**, должны иметь проверенный общий причинный
конец 5m/15m истории перед fresh WS attestation; независимые
Strategy/account/instrument consumers не блокируются чужим недостающим symbol.
Пересечение границы свечи требует
дозагрузки хвоста и новой проверки, а не допуска по устаревшему окну. Source,
UTC, closed-only, count/gap/immutability и signed proof lifetime не ослабляются.
Historical candles не создают native trade cursor. Ожидание ограничено finite
attempt/deadline budget; исчерпание отображается как `BLOCKED` с причиной,
без бесконечного «подождите». Integrity/identity/ambiguous mutation и иные
неожиданные runtime exceptions остаются настоящими durable faults.

Этот cold preparation path не заменяет open-position recovery: подтверждённые
позиции сохраняют exact ownership/protection/Exit, а Entry NOT READY не
отключает их сопровождение. PREPARING/BLOCKED никогда не создаёт OWNER ARM,
не открывает gate и не считается техническим READY. Исторические faults не
удаляются и не закрываются автоматически вследствие этой классификации.

## 7.6 Incident visibility отдельно от допуска Entry — OWNER DECISION 2026-11-04

Owner допуска — TC §4.13; architecture capability matrix — ARCH §55.
Monitoring/Lifecycle/notification owners обязаны сохранять, доставлять и
показывать проблемы независимо от того, блокируют ли они текущий вход.
Неотправленный historical OWNER_WEBHOOK, unread notification, OPEN severity
label, historical report/close-attribution defect либо отсутствие ACK не
являются самостоятельным запретом новых Entry.

Read-model раздельно показывает current execution readiness и журнал ремонта.
У каждого incident есть scoped Entry-impact classification, текущий evidence
и связь с конкретным обязательным condition либо доказанный NON_BLOCKING
статус. Consumer не вправе выводить global block из общего fault/delivery count.
Отсутствующий mandatory proof означает scoped NOT READY, не optimistic PASS.

Actual current protection/Exit/exposure/identity/uncertain-mutation fault
по-прежнему блокирует затронутый Entry scope и запускает соответствующий repair;
existing position/Exit не прекращается из-за запрета новых входов. Нельзя
отмечать прочтение/доставку либо resolution без реального события только для
снятия gate. Новый classifier не переписывает исторические записи.

Dashboard обязан показывать точный текущий blocker отдельно от прошлых ошибок,
affected account/symbol/dependency и условие восстановления. Readiness progress
имеет одного presentation owner; timer и poll не перебивают друг друга.
Исторический RED может оставаться RED в журнале, но не должен выдаваться за
доказательство текущего global execution block.

Статус: CANON target; implementation/cutover обязательны по DEV §54.

Уточнение OWNER DECISION 2026-11-04 (TC §4.13.5): закрытые сделки влияют
на новые Entry исключительно через фактические HARD_STOP события применимого
SECOND48 exact StrategyVersion. Другие причины закрытия, UNKNOWN и неполная
атрибуция/история/доставка не являются admission blockers. Для них не требуется
отдельный proof отсутствия влияния. Текущие exposure/команды/ресурсы проверяются
независимо; исторические ошибки остаются видимыми без подделки фактов.

## 7.7 Временная недоступность PUBLIC_TRADE audit — 2026-11-04
SSL/network timeout после двух bounded REST попыток не доказывает потерю native
событий. Он переводит Observer в RECONNECTING/ready=false через ту же bounded
transport recovery policy, что WebSocket disconnect. Новая epoch допускает Entry
только после штатного причинного восстановления. Три transport failures за 60s
создают durable ERROR; malformed/negative/unknown continuity proofs не считаются
сетевым ожиданием. Исторический fault не стирается последующим RUNNING.

## 7.8 Observer работает, Entry отказан — 2026-11-05
OPERATIONAL_SAFETY_BLOCKED для одного account Entry — результат допуска, а не
UNIVERSAL_ENTRY_OBSERVER_RUNTIME_ERROR. Сохранять точный reason/scope без потери
market checkpoint и без остановки независимого Exit. Реальные source/identity
нарушения сохраняют durable ERROR и existing runtime guard.
Trade audio выдаётся только по подтверждённому актуальному событию позиции;
operational gate/CRITICAL предупреждение должно быть явно отдельным типом и не
использовать тот же тройной звук. История не генерирует повторную тревогу при
первой загрузке/восстановлении вкладки. Цвет журнала не означает OPEN gate.

## 7.9 Gateway preparation recovery — OWNER DECISION 2026-11-05

Near-boundary REST OHLCV is provisional until the approved adapter completes
its bounded closed-tail confirmation. Expected finality lag is PREPARING, not
an incident. A real immutable conflict is persisted once, then BLOCKED with its
exact market identity/root reason; it is not retried into a generic deadline.
Only a newer active authenticated readiness request for the same installed
release/scope may reset a terminal preparation budget. GET/status polling,
old requests and passage of time alone cannot do so. This is in-process cold
preparation, not expansion of the START-only Service Broker to RESTART.
Existing positions/uncertain mutations retain separate strict recovery owners.

## 7.10 PostgreSQL-first MarketSource/Observer contract — OWNER DECISION 2026-11-07

**Роль:** Observer — техническая обслуживающая система: approved Bybit
Public WebSocket (native `publicTrade`, kline), approved Bybit Public REST,
проверка MarketSource/InstrumentIdentity, сбор и атомарное сохранение фактов
в CRIPTA PostgreSQL, покрытие/свежесть/повторное подключение, read model
исправности и доставка exact facts потребителям. Observer **не** является
Strategy, Entry/Exit Decision, execution-admission owner, финансовым gate writer
или самостоятельным issuer общего торгового запрета. Разделение owners —
ARCH §60, допуск — TC §4.13/§4.18.

### 7.10.1 Обязательная доступность данных и bounded repair

На каждый участвующий EntryPlan, ExitPlan, MFE либо market/research consumer
существует конкретный `required-data manifest`: physical writer/reader,
canonical PostgreSQL table/effective immutable view, MarketSource/Instrument,
symbol/timeframe/event type, точный lookback и ATR seed, UTC boundary,
min/max evidence time, source contract, count/coverage/gaps/duplicates,
freshness TTL, checkpoint identity, durable retention, repair owner и deadline.
Manifest — **проверяемый контракт, не новый глобальный фильтр**; точные
числа определяются действующими планами и существующим TC, не Observer.
Слепое ожидание заполнения live окна при уже сохранённой истории запрещено.

Порядок подготовки:
1. прочитать PostgreSQL и вычислить exact needed slice/coverage;
2. если он complete и current — передать его потребителю без ненужного REST;
3. если есть gap — bounded approved Bybit REST fetch **только gap/tail**,
   closed-only и immutable-identity check, затем один canonical physical write
   с provenance и independent readback;
4. если authentic provider не может дать нужные native `seq/execId` —
   сохранить точный gap/repair evidence, не фабриковать факты и не выдавать
   потерянную причинность за восстановленную;
5. публиковать отдельные состояния `READY`, `PREPARING`,
   `RECONNECTING`, `BLOCKED`, `ERROR` **по dependency/affected scope**:
   expected preparation не durable runtime fault; source corruption —
   явный typed incident. Новое реальное Entry не допускается без
   необходимых ему mandatory facts, но unrelated consumers продолжаются.

Исторические недостающие факты, доступные достоверно, восстанавливаются
для анализа даже после истечения торгового сигнала; `EntryAttempt`/audit
хранят original event time, source and replay provenance. Observer не выдаёт
задним числом биржевые ордера, не меняет immutable Strategy/EntryPlan/ExitPlan.

### 7.10.2 Exact native tape и PostgreSQL checkpoint

Закрытые свечи `analytics.closed_candles`, `analytics.market_minutes`,
снимки MAYAK/Dispatcher и агрегаты **не** являются эквивалентом
отдельных `publicTrade` `seq/execId`. Для активных exact native
Entry/Exit/MFE consumers MarketSource producer обязан обеспечивать
**ограниченный обязательствами потребителей durable publicTrade journal**
в production PostgreSQL с exact source identity, UTC event_time, price,
qty, side, native sequence and execId, source/provenance, ingest time,
dedup/ordering/QC и durable cursor. Физическая схема/индексы/retention и
атомарная `journal-before-processing-checkpoint` фиксация вводятся
проверенной migration. Не принимать лишь память WebSocket buffer за
restart storage. Append-only native audit, pin открытых позиций и causal
Exit не подпадают под простое 24h удаление рекомпьютируемых полных JSON
контекстов. Но это **не** разрешение бесконтрольного raw market lake:
горизонт и ёмкость обоснованы конкретными active consumers и
оставшимися обязательствами.

На disconnect/restart separate evidence фиксирует previous epoch,
gap start/end, last `seq/execId` и все same-seq IDs, actual Bybit WS
session/ACK и новый verified cursor. Исторический gap audit не уничтожается
обнулением счётчиков; lifetime gaps не должны автоматически превращать
каждую независимую доказанно новую session в неисправную.
Если точная ретроспективная native последовательность недоступна через
текущий recent-trade API, проверять другие approved authentic archives
и не называть одно API доказательством «данные потеряны».
Ни `closed_candles`, ни агрегаты не синтезируют native `seq`.

**Owner clarification 2026-11-07 к §7.10.2 (Issue #470).** Observer публикует отдельно PRICE_HISTORY_VERIFIED (аутентичная price-time/trade-ID история и полное покрытие exact необходимого интервала), NATIVE_IDENTITY_VERIFIED (проверенные seq/execId для нужных causal points), NATIVE_CONTINUITY_VERIFIED (только если полный native replay требуется конкретному consumer), EXIT_READY (только независимый Position/Exit owner). Ни один из этих статусов не следует автоматически из другого. Bounded Bybit recent-trade <=1000 — optional fast path, а не универсальное условие восстановления после долгого простоя. PostgreSQL-first, затем проверенные issuer/hash/interval/dedup/provenance внешние архивы и approved retrieval; архивные trdMatchID/time/price без seq достаточны только для доказанных price-history задач, не для выдуманного native seq. Исторический gap не разрушает новую подтверждённую WS epoch unrelated scopes; при OPEN недоказанный обязательный Exit/MFE факт остаётся BLOCKED только в affected scope, Exchange protection сохраняется.

### 7.10.3 Позиция прежде нового Entry; fault scope

Существующая позиция/ее protection — отдельный приоритетный consumer:
signed Bybit Private MAIN/SUB inventory и original
StrategyPosition/ExitPlan/MFE/Execution ownership восстанавливаются
соответствующими account/Position/Exit owners, **не** Observer.
Observer обеспечивает требуемые market facts для сопровождения, но
отказ нового Entry по account permission/capital/SECOND48 не прерывает
market ingest, checkpoint и Exit handoff.

Одна ошибка native восстановления OPEN-position не должна автоматически
убивать общий transport/MarketSource worker, если независимая доставка
текущих фактов и другие consumers доказанно исправны. Ошибка обязательного
shared physical source integrity локализуется по доказанной shared
dependency, а не маскируется обычным admission denial. Existing
account latch/unknown exposure/required protection остаются у своих
owners. Состояние `observer_ready=false` — health evidence, **не**
самостоятельный blanket financial veto; конкретный admission verdict
публикует общий scope-aware classifier (ARCH §55, TC §4.18).

### 7.10.4 Read-model, errors, acceptance

Dashboard/оператор видит per-symbol/source/consumer: actual SQL slice,
required vs actual UTC bounds/count/coverage, native last cursor/epoch/gap
и причина отсутствующего proof; per-account: актуальный signed snapshot,
open position/SL/TP, Exit ownership и отдельный account safety blocker.
Исторические incidents/notifications/audit не удаляются и не помечаются
RESOLVED без фактического события. Бесконечный общий 2s retry
после невосстановимого native anchor запрещён: конечная classified
repair работа со статусом `WAIT_FOR_EVIDENCE`/BLOCKED и независимым
продолжением не затронутых consumers.

Обязательные tests: PG-first warmup PREV7D/SECOND48, gap REST ingest,
original immutable store, native journal+checkpoint atomicity and
same-seq, 21h gap with exact archive vs unavailable proof, signed
protected position first, Exit on Entry refusal, per-account/per-symbol
isolation, source disconnect, restart, release/provenance/TTL and UI
scoped read-model parity. Owner LIVE-arm/permissions не выдаются.

Статус: `OWNER_DECISION=YES`; `CANON=YES AFTER MERGE`;
`IMPLEMENTED=NO`; `DEPLOYED=NO`; `LIVE_CHANGE=NO`.
