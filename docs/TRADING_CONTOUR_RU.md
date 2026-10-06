# CRIPTA — торговый контур: STRATEGY / ENTRY / EXIT / EXECUTION

**Версия:** 2.3
**Дата:** 2026-10-06
**Статус:** активный канонический контракт торгового контура

Этот документ объединяет правила четырёх связанных частей торгового контура:
Strategy, Entry, Exit и Execution. Верхняя архитектура определяется
`docs/CRIPTA_ARCHITECTURE_RULES_RU_*.md`, терминология — `docs/CRIPTA_GLOSSARY_RU*.md`.

# 1. STRATEGY — владелец торгового смысла и планов

Strategy layer владеет торговым смыслом и lifecycle утверждённой Strategy.

Пассивным immutable справочником является `StrategyCard`. Сам торговый смысл
не живёт внутри Entry/Exit Engines.

Внутри Strategy layer существует `Strategy Materializer`, который
детерминированно создаёт `EntryPlan` и `ExitPlan` exact Strategy version и
публикует их в устойчивый active-plan registry. Materializer не наблюдает рынок
и не создаёт `StrategySignal`/ExitDecision.

## 1.1 StrategyCard

`StrategyCard`:
- утверждается владельцем;
- неизменяема после утверждения;
- имеет version/fingerprint;
- не меняется автоматически от статистики;
- содержит все параметры, влияющие на решение и исполнение конкретной Strategy.

Изменение любого торгового параметра означает новую версию Strategy.

## 1.2 Что принадлежит Strategy

Strategy может определять минимум:
- symbols/universe;
- direction;
- Entry geometry;
- timeframe;
- временную глубину геометрии;
- ширину/формулу зон;
- правила совмещения нескольких геометрий;
- stabilization minutes;
- touch/retest/count/sequence;
- cooldown/reset/embargo;
- MAYAK/Dispatcher context consumption;
- capital allocation;
- amount/size;
- leverage;
- execution order policy;
- protection;
- hard stop/TP/BE/trailing;
- holding;
- Exit geometry/context rules;
- hedge, если он включён.

Любой real hedge additionally requires a compatible Exchange position-mode
contract. В current one-way same-symbol hedge unsupported и fail-closed.

Если параметр отсутствует, Entry/Exit/Execution не имеют права подставить
историческое торговое значение по умолчанию.

## 1.3 H9/H3 как параметры Strategy

H9 и H3 — названия геометрий по временной глубине, а не глобальные константы
системы.

Текущая исследуемая первая Strategy использует H9:
- 5m на глубине 9 часов;
- 15m на глубине 9 часов;
- их совмещение по правилам Strategy.

Это не означает, что любая будущая Strategy обязана иметь H9.

H3:
- 5m на глубине 3 часа;
- 15m на глубине 3 часа;
- их совмещение.

H3 сейчас не является Entry condition текущей первой Strategy.

Owner-confirmed baseline 2026-09-25: H9=540m (108x5m+36x15m), H3=180m (36x5m+12x15m); одинаковая формула extrema/zones; Wilder ATR200 отдельно на каждом timeframe; zone half-width=ATR*0.5. `gap/confluence` Strategy-owned, baseline gap=0. Legacy shock/reset/cooldown не входит в baseline. Сначала проверяется exact baseline equivalence, затем отдельно исследуются ATR period/multiplier и иные варианты.

Strategy владеет GeometrySpec. Один exact `symbol + GeometrySpec fingerprint` может причинно рассчитываться/храниться наблюдательным контуром как versioned Geometry timeline и переиспользоваться Entry/Exit/Position Supervisor/Analyst. Изменение result-affecting параметра создаёт другой fingerprint. Наблюдательный контур не выбирает торговые параметры и не получает trading policy.

## 1.4 Стабилизация

Время стабилизации задаётся в Strategy в минутах отдельно там, где оно нужно.

Никакое найденное исследованием значение не становится значением по умолчанию
универсального Entry Engine.

## 1.5 Несколько Strategy

Несколько Strategy могут одновременно создать независимые и даже
противоположные StrategySignal. Entry Engine не выбирает между ними по качеству
или направлению.

Для real mutation действует отдельный physical-slot admission contract.
Текущий Bybit Unified linear one-way contract допускает один active owner
lifecycle на account + symbol + positionIdx=0.

Если exclusive slot claim получить нельзя, attempt получает
EXCHANGE_POSITION_OWNERSHIP_CONFLICT и real EntryExecutionRequest не создаётся.

Встречный StrategySignal не закрывает и не неттирует чужую StrategyPosition.
Analyst может сохранить его только как counterfactual с exact block reason.

Same-symbol hedge в one-way режиме unsupported и fail-closed. Hedge-mode,
subaccount isolation или внутренний netting требуют отдельного owner-approved
contract.

## 1.6 Материализация

Из exact StrategyCard/version `Strategy Materializer` создаёт immutable
`EntryPlan` и `ExitPlan`.

Материализация выполняется при activation/load/restart/recovery в тех местах,
где это требуется реализации. Materializer может быть функцией, классом или
service; это implementation detail внутри Strategy layer, а не новый
верхнеуровневый слой.

Каждый план обязан нести exact:
- strategy_id;
- strategy_version;
- strategy_fingerprint;
- собственный plan fingerprint.

Любое поле, влияющее на решение или исполнение, должно иметь доказанный
сквозной consumer path. Неподдержанный параметр означает fail-closed для
активации, а не silent ignore.

## 1.6.1 Деактивация Strategy и открытая позиция

Выключение StrategyActivation запрещает новые Entry по этой activation, но не
переписывает и не осиротевляет уже открытую StrategyPosition.

Открытая позиция до final close продолжает использовать exact Strategy version,
ExitPlan, protection/emergency policy и lineage, которые были привязаны при её
открытии. Новая версия Strategy не может подменить ExitPlan существующей
позиции.

## 1.7 Исследование

Исследование никогда не меняет Strategy автоматически.

Результат становится торговой policy только после отдельного решения владельца
и создания новой Strategy version.

## 1.8 Strategy settings и экспериментальные версии

Настройки конкретной Strategy не хранятся в глобальных defaults и не
дублируются в отдельном непрозрачном `strategy_settings`-мешке. Они
раскладываются по owner-owned policy-блокам StrategyCard:

- `entry_policy` — условия и параметры Entry;
- `touch_policy` — касания/retest/cooldown;
- `capital_policy` — капитал/leverage;
- `protection_policy.initial_protection` — базовая биржевая защитная рамка,
  известная уже при Entry;
- `exit_policy` — динамические правила сопровождения/Exit;
- `lifecycle_policy` — lifecycle/hedge и другие сквозные правила;
- MAYAK/Dispatcher policies — только явно разрешённый Strategy-specific
  consumption context.

Значения, которые ещё исследуются, сначала живут в Strategy Candidate/Draft.
Если нужно провести воспроизводимый shadow/MICRO_LIVE эксперимент, владелец
утверждает точный снимок как новую immutable StrategyCard/version. Это
утверждает только конкретный экспериментальный снимок и не превращает его
числа в глобальные defaults или обязательный Exit для будущих Strategy.

Базовая защитная рамка и динамический Exit — разные сущности. Например,
временный hard stop/верхняя защитная граница могут быть переданы в
`EntryExecutionRequest` как initial protection, даже если H3/касания/BE/
trailing ещё исследуются и не утверждены как динамический ExitPlan.

Даже если защитные границы известны в момент Entry, их owner остаётся Strategy
через `protection_policy`, а не Entry Engine.

Если lifecycle_policy.hedge включён, authoring/materialization/readiness обязаны
проверить совместимый Exchange position mode. В current one-way same-symbol
hedge не может быть сохранён как silently executable.

Для нового Strategy authoring:
- каждый поддерживаемый setting имеет явный `enabled`;
- disabled setting не несёт скрытого торгового числа;
- authoring template не содержит числовых trading defaults;
- неподдержанный decision/execution-affecting setting нельзя silently сохранить
  как исполняемый: authoring/materialization/readiness обязаны fail-closed.

## 1.9 Strategy-owned market context interpretation

Торговый смысл объективного MAYAK/Dispatcher context принадлежит только exact
Strategy version.

Каждая StrategyCard, которая может читать market context, обязана явно
декларировать usage отдельно для фаз ENTRY, OPEN_POSITION/POSITION и EXIT.

Для каждой фазы declaration различает минимум:

~~~text
NONE
OBSERVE_ONLY
POLICY
~~~

Пустой/отсутствующий список context requirements не считается доказательством
осознанного NONE.

Если фаза имеет POLICY, exact Strategy version хранит versioned Strategy
Context Policy с feature/context id, required scope, causal freshness/quality
requirement, comparator/condition, missing/stale behavior, resulting Strategy
decision/action и exact version/fingerprint.

Один и тот же MarketRegime разные Strategy вправе трактовать противоположно.
MAYAK/Dispatcher не могут сами добавлять эту интерпретацию.

Research result также не меняет её автоматически:

~~~text
MARKET FACT / CONTEXT
-> RESEARCH / OOS
-> OWNER DECISION
-> NEW STRATEGY VERSION
-> TEST / SHADOW
-> LIVE EQUIVALENCE
-> MICRO_LIVE
-> LIVE
~~~

Materializer/readiness обязаны fail-closed, если Strategy заявляет POLICY, но
поддержка/consumer path required context не доказаны.

## 1.10 R1 — owner-approved implementation candidate

OWNER DECISION 2026-10-04: `R1` остаётся current label исследованной L5-3 Strategy Candidate, но ниже зафиксирован exact implementation target, разрешённый для реализации и SHADOW/test validation. Это всё ещё не production Strategy ID и не разрешение MICRO_LIVE/LIVE.

```text
GEOMETRY
  L5-3 = rolling 36 fully closed 5m candles
  ATR = Wilder ATR200
  half_width = 0.5 * ATR200
  strict STAY = all 6 finalized states have equal opposite structural boundary

ENTRY SIGNAL
  LONG  = touch current lower_inner
  SHORT = touch current upper_inner
  entry-side structural boundary stable >= 6 finalized 5m states
  current working_width_pct_lower >= 1%

ENTRY EXECUTION
  signal first; no pre-confirmation order
  ordinary FLAT Entry = PostOnly LIMIT_OFFSET 0.10% favorable from confirmed calculated R1 Entry price:
    LONG  = Entry * (1 - 0.0010)
    SHORT = Entry * (1 + 0.0010)
  PostOnly must fail/cancel rather than cross as taker
  no time-based taker fallback
  pending Entry is cancelled/skipped when its exact signal level changes, R1 rule becomes invalid, the opposite target is reached before fill, or an opposite qualified signal supersedes it

OCCUPANCY / PING-PONG
  same-symbol ONE_WAY ownership remains mandatory
  while occupied, same-side qualified Entry is ignored
  first qualified opposite Entry keeps baseline hard-flip semantics:
    immediate taker close of current position
    immediate taker open of the opposite position
  this hard-flip path does not receive maker-delay optimization

EXIT
  dynamic target = current opposite L5-3 inner boundary
  when price reaches an already-resting target -> reduce-only LIMIT maker TP
  when a newly recalculated target is already marketable -> immediate taker close
  any owner-approved hard/forced close remains immediate taker
  no trailing/chase/maker-wait delay is allowed for hard exits

TERMINAL REPLAY
  unresolved final position is not force-closed
```

Research evidence checkpoint 2026-10-04:
- strict all-six STAY produced the same event/economics result as the earlier endpoint-STAY replay on the tested OLD90 + RECENT14 raw datasets;
- hard stops at -1.0%, -1.5% and -2.0% worsened aggregate R1 economics and are not part of this implementation target;
- confirmed-entry PostOnly offsets 0.05%, 0.10% and 0.20% were replayed with causal occupancy; 0.10% was the strongest aggregate candidate across both the current first-five cohort and all 15 screened symbols;
- pre-confirmation fifth-state maker placement is rejected: it changed Entry semantics and produced premature invalid fills;
- delayed maker conversion of hard exits is rejected by OWNER DECISION 2026-10-04 because future adverse movement is unbounded relative to the small fee saving; hard exits remain immediate taker.

The current first-five R1 cohort remains: `APTUSDT / INJUSDT / DOTUSDT / LTCUSDT / ARBUSDT`.

R1 implementation does not satisfy real-arm readiness by itself. Before any real Exchange mutation, an owner-approved initial loss-containment contract, exact immutable StrategyCard/version, implementation/replay equivalence, SHADOW evidence, LIVE EQUIVALENCE, MICRO_LIVE and all gates from §4.7 remain mandatory. Until that chain is complete, mainnet stays disarmed.

## 1.10.1 R1 — documentation-to-implementation mapping

DOCUMENTATION RESEARCH 2026-10-04: exact R1 contract из §1.10 сопоставлен с
текущими StrategyCard / EntryPlan / ExitPlan / Execution contracts и с
research lineage из `RESEARCH_COMPUTE §19`.

### Что сохраняется без изменения торговой семантики

R1 реализуется как **новая immutable StrategyCard**. Существующие
`entry_v1_monitor_*` и `experimental_h9_h3_*` не редактируются и не
переиспользуются как R1.

OWNER DECISION 2026-10-04: R1 разворачивается как **пять отдельных
immutable StrategyCard — по одной на каждый symbol**, при этом каждая карточка
владеет обоими направлениями LONG+SHORT и полным ping-pong lifecycle:

```text
r1_aptusdt  -> symbols=[APTUSDT] -> direction_policy=[LONG, SHORT]
r1_injusdt  -> symbols=[INJUSDT] -> direction_policy=[LONG, SHORT]
r1_dotusdt  -> symbols=[DOTUSDT] -> direction_policy=[LONG, SHORT]
r1_ltcusdt  -> symbols=[LTCUSDT] -> direction_policy=[LONG, SHORT]
r1_arbusdt  -> symbols=[ARBUSDT] -> direction_policy=[LONG, SHORT]

capital_policy per Strategy:
  requested_amount = 10 USDT
  leverage = 1x

one-way physical slot:
  account/product/symbol/positionIdx=0
  one active R1 position per symbol
```

Это сохраняет исследованную семантику: LONG и SHORT **не разделяются на две
независимые Strategy одного symbol**. Opposite Entry остаётся transition одной
StrategyPosition внутри той же per-symbol StrategyCard. Разделение на отдельные
LONG-card и SHORT-card запрещено для R1 v1, потому что создало бы конкуренцию за
один physical slot и изменило бы исследованный ping-pong lifecycle.

Сумма 10 USDT и leverage=1x принадлежат StrategyCard, а не общей online-trading
настройке. Их последующее изменение требует новой immutable Strategy version,
если меняется торговая/капитальная policy.

R1 EntryPlan должен материализовать:

```text
watch geometry:
  timeframe = 5m
  rolling fully closed candles = 36
  ATR = Wilder ATR200
  zone_half_width_atr = 0.5
  strict STAY states = 6
  working_width_pct >= 1%

LONG  touch = lower_inner
SHORT touch = upper_inner

flow/OI/hourly-swing/cooldown/legacy confluence filters = OFF
pre-confirmation placement = FORBIDDEN
```

R1 Entry execution policy:

```text
confirmed signal
-> LIMIT_OFFSET 0.10% favorable
-> PostOnly
-> maker fill or no Entry

LONG  limit = confirmed Entry * 0.999
SHORT limit = confirmed Entry * 1.001
```

Для R1 **numeric time TTL не является исследованным торговым правилом**.
Pending Entry живёт только пока exact R1 signal остаётся действительным и
должен быть отменён при любом из условий из §1.10: exact signal level changed,
R1 invalidated, opposite target reached before fill, opposite qualified signal
superseded. Existing generic implementation requirement
`entry_limit_ttl_seconds > 0` therefore cannot be silently reused for R1;
implementation must support signal-validity lifetime without inventing an
untested timeout. No timeout-to-taker fallback is allowed.

R1 ExitPlan:

```text
normal target:
  current opposite L5-3 inner boundary

already-resting target price hit:
  reduce-only LIMIT maker TP

new causal geometry makes recalculated target already marketable:
  immediate MARKET / taker close

opposite qualified Entry while occupied:
  immediate MARKET / taker close current
  immediate taker open opposite
  same Strategy / same symbol / one-way transition

hard-stop / break-even / trailing / liquidation exit / time exit:
  not part of current R1 implementation target
```

Waiting for a maker price on a hard exit is explicitly forbidden for current R1.
Historical event-study improvement is not promoted because the adverse tail
during waiting is not bounded by the small fee saving.

### Required implementation deltas before an exact R1 card can run

Current code is not yet equivalent to the contract above. The implementation
pass must close these exact gaps rather than changing R1 semantics:

1. **Strategy authoring:** current new-card validator allows exactly one
   direction. It must allow combined `LONG + SHORT` for the explicit
   bidirectional R1 contract without relaxing unrelated Strategy validation.
2. **Entry geometry:** current generic market watch does not yet have the exact
   strict-six L5-3 operator required by R1.
3. **PostOnly Entry:** current `LIMIT_OFFSET` runtime path sends `GTC`.
   R1 needs explicit PostOnly propagation through StrategyCard -> EntryPlan ->
   EntryExecutionRequest -> Execution.
4. **Pending Entry invalidation:** current numeric TTL mechanism is insufficient.
   R1 needs cancellation by exact Strategy signal validity, with no invented
   numeric timeout and no taker fallback.
5. **Dynamic maker TP:** `local_zone_exit` exists in authoring/PAPER surfaces
   but current readiness marks it not wired for LIVE. R1 needs exact
   opposite-inner target ownership, resting reduce-only maker order and causal
   replacement.
6. **Close-and-reverse:** opposite R1 Entry while a same-symbol position is
   owned must be an atomic/auditable one-way lifecycle transition. A normal
   competing Entry admission must not race the still-owned slot.
7. **Hard exit:** marketable target reprice / forced close remains immediate
   taker. No trailing/chase/PostOnly wait is added.

### Protection and activation boundary

Research did not select a trading hard-stop at `-1.0/-1.5/-2.0%`;
those variants worsened R1 economics and remain rejected as normal Exit policy.

OWNER DECISION 2026-10-04 introduces a separate **catastrophic initial
loss-containment guard** for real-money R1:

```text
initial catastrophic stop = -10.0% from actual filled Entry
execution = immediate MARKET / taker
scope = every R1 StrategyPosition
purpose = terminal safety only; not normal R1 Exit optimization
```

This `-10%` guard is not research evidence of optimal Exit. It is the
owner-approved terminal loss-containment path required for MICRO_LIVE and must
not alter dynamic opposite-inner TP, PostOnly Entry, or ping-pong semantics.

OWNER DECISION 2026-10-04 replaces the previous DEMO prerequisite. Current R1
rollout is:

```text
IMPLEMENT
-> TEST
-> IMPLEMENTATION / LIVE EQUIVALENCE
-> MICRO_LIVE on mainnet
-> runtime behavior evidence
-> owner review before any larger limits
```

MICRO_LIVE limits for R1 v1:

```text
strategies = exactly 5:
  r1_aptusdt / r1_injusdt / r1_dotusdt / r1_ltcusdt / r1_arbusdt
requested_amount per Strategy = 10 USDT
leverage = 1x
maximum simultaneous requested capital = 50 USDT
position mode = ONE_WAY
positionIdx = 0
other Strategy activations = disabled
```

The five immutable R1 cards may be armed on mainnet only after all §4.7 gates
are proved against the exact published/deployed release. Owner approval in this
section satisfies the **policy decision** to proceed to MICRO_LIVE, but it does
not waive any technical fail-closed gate, identity check, reconciliation check,
or release-evidence requirement.

No existing legacy stop, TP, BE or trailing setting may be inherited as an R1
default. Any change to `10 USDT`, `1x`, the five-symbol cohort or the `-10%`
catastrophic guard requires a new immutable Strategy version / owner decision
as applicable.

# 2. ENTRY — универсальный Entry Engine

## 2.1 Назначение

Entry Engine — универсальный активный исполнитель EntryPlan.

Каноническая схема:

```text
НОРМАЛИЗОВАННЫЕ ПРИЧИННЫЕ ФАКТЫ РЫНКА
        +
АКТИВНЫЕ ENTRY PLAN
        ↓
ENTRY ENGINE / CAUSAL MARKET WATCH
        ↓
СОВПАДЕНИЕ УСЛОВИЙ КОНКРЕТНОГО ПЛАНА
        ↓
STRATEGY SIGNAL
        ↓
ATTEMPT / ENTRY DECISION
        ↓
optional EXECUTION REQUEST
```

## 2.2 Strategy не создаёт signal как процесс

StrategyCard является пассивной policy.

Entry Engine сам фиксирует `StrategySignal`, когда причинные рыночные факты и
разрешённый/обязательный context удовлетворяют конкретному активному EntryPlan.

## 2.3 Независимость планов

Для каждого market fact Entry Engine рассматривает все активные EntryPlan,
относящиеся к symbol.

Разные Strategy имеют отдельные состояния, могут иметь разные Entry и
противоположные направления и создают разные `signal_id`.

Entry не вводит winner/priority/arbitration между Strategy.

## 2.4 Entry не владеет торговыми числами

В коде Entry допустимы только универсальные операции и техническая механика.

Торговые значения приходят через EntryPlan:
- lookback/depth;
- geometry formula;
- stabilization;
- touch;
- gap/confluence;
- cooldown/reset;
- sensors/context;
- amount/leverage/execution policy;
- lifetime/TTL;
- другие Strategy-owned поля.

Запрещены скрытые глобальные H9/H3/130 bars/30m/60m или иные исторические
торговые значения по умолчанию.

## 2.5 Entry zone / Entry point

`Entry zone` и `Entry point` — общие понятия, а не одна универсальная формула.

Текущая первая исследуемая Strategy может формировать вход через совмещение
H9 5m + H9 15m. Другая Strategy может использовать иной способ.

Поэтому Entry Engine не должен предполагать:
`Entry == H9`.

Если Strategy использует зональную геометрию, физические объекты называются
нейтрально:
- нижняя граничная зона;
- верхняя граничная зона;
- рабочий диапазон;
- внутренняя граница;
- внешняя граница.

LONG/SHORT задаёт роль этих объектов только на уровне Strategy.

## 2.6 EntryDecision и границы сущностей

Канонический перечень token -> entity определяется только
docs/CRIPTA_GLOSSARY_RU*.md.

Минимальные EntryDecision outcomes:
- ACCEPTED;
- STRATEGY_CONDITION_REJECTED;
- INSUFFICIENT_AVAILABLE_FUNDS;
- EXCHANGE_POSITION_OWNERSHIP_CONFLICT;
- OPERATIONAL_SAFETY_BLOCKED;
- STALE_OR_UNKNOWN_REQUIRED_STATE;
- EXPIRED;
- CANCELLED.

ACCEPTED разрешён только после successful real Entry admission.

EXCHANGE_POSITION_OWNERSHIP_CONFLICT — штатный admission outcome, а не
lifecycle fault.

EntryDecision.EXPIRED/CANCELLED относятся к strategy_attempt до принятого request.
После ACCEPTED EntryExecutionRequest использует собственные request-state
tokens, например REQUEST_PENDING / REQUEST_DISPATCHED /
REQUEST_ACKNOWLEDGED / REQUEST_EXPIRED / REQUEST_CANCELLED /
REQUEST_RECONCILIATION_REQUIRED / REQUEST_TERMINAL.

## 2.7 Real Entry admission: slot claim + capital reservation

Обязательный порядок и атомарность определены ARCH §5.1.

Смысл:

```text
required account / position-mode validation
-> atomic physical slot claim
-> atomic capital reservation
-> EntryDecision
```

Успешные slot claim и reservation должны быть зафиксированы all-or-nothing.
Если reservation не получена, slot claim откатывается/освобождается в том же
admission transaction. Unknown post-dispatch state не освобождает claim или
reservation до reconciliation.

OWNER DECISION 2026-10-06: required account/capital state real Entry берётся
только из current AccountStateGeneration=COMPLETE. Generation объединяет
wallet/account type, positions, active orders, available capital и exact
position-mode refs active real symbols. Новый COLLECTING не портит предыдущий
COMPLETE; новый terminal FAILED блокирует admission до следующего COMPLETE.

Для generation-backed real Entry не применяются независимые секунды
wallet_age, reconciliation_age или capacity_age. Поле
capacity_max_age_seconds, присутствующее в historical/current StrategyCard
для совместимости, не является real-admission gate при наличии
AccountStateGeneration. StrategySignal / EntryExecutionRequest expiry остаётся
отдельным Strategy-owned временным контрактом.


Physical claim обязан иметь durable identity минимум:
exchange_position_slot_claim_id, exchange_position_key, strategy_attempt_id,
strategy_id/version, direction, claim_state, claimed_at, released_at,
release_reason.

Для replay фактический winner определяется только durable claim/reservation
ordering. Если exact ordering отсутствует, результат помечается UNKNOWN, а не
восстанавливается по ближайшим timestamps.

Position-mode state является required account state. Fresh verification
обязательна при real activation/re-arm, добавлении symbol, recovery/reconnect
без доказанной continuity и после обнаруженного Exchange configuration change.
Для каждого production real Entry exact mode proof берётся из current COMPLETE
AccountStateGeneration.

Текущий утверждённый contract:
ONE_WAY + positionIdx=0. Unknown/stale -> STALE_OR_UNKNOWN_REQUIRED_STATE.
Свежий, но несовместимый mode/positionIdx -> OPERATIONAL_SAFETY_BLOCKED с
block_reason=EXCHANGE_POSITION_MODE_MISMATCH.

Для production generation-backed admission fresh означает exact
position-mode ref current COMPLETE AccountStateGeneration для данного symbol,
а не отдельное истечение секундного fresh_until. fresh_until может сохраняться
как provenance/compatibility для activation/recovery и historical контуров, но
не является параллельным per-entry clock gate.

## 2.8 После fill

После confirmed open fill создаётся logical StrategyPosition с exact
Strategy/EntryPlan/ExitPlan lineage и фактическими exchange/order/fill refs.

Durable physical slot claim переходит от strategy_attempt к exact
StrategyPosition и остаётся активным до final flat/reconciliation.

Entry больше не сопровождает позицию. Entry price и causal snapshot сохраняются
как исторические факты.

# 3. EXIT — универсальный Exit Engine

## 3.1 Назначение и ownership

`Exit Engine` — универсальный активный исполнитель `ExitPlan`.

Количество Strategy ему не важно. Он получает StrategyPosition + exact
ExitPlan, наблюдает только разрешённые этим планом причинные facts/context и
создаёт `ExitDecision` при выполнении конкретного правила.

После confirmed fill сопровождение принадлежит ExitPlan той же exact Strategy
version, которая открыла позицию.

Exit Engine обязан claim/acknowledge StrategyPosition. Entry больше не владеет
позицией.

## 3.2 Неизменяемая точка входа

Фактическая `Entry price` — исторический факт состоявшегося входа.
Она не двигается вслед за рынком.

Снимок геометрии/фактов момента Entry хранится для аудита и причинности.

## 3.3 Текущая геометрия после Entry

Текущая геометрия Strategy продолжает причинно пересчитываться после входа.

Для H9 это означает, что она может:
- двигаться вверх;
- двигаться вниз;
- оставаться стабильной;
- менять граничные зоны/рабочий диапазон;
- изменяться многократно в течение одной позиции.

Движение может возникать как от 5m-, так и от 15m-компоненты.

Сопровождение анализирует актуальную геометрию относительно:
- фиксированной Entry price;
- предыдущего состояния;
- текущей цены;
- состояния позиции.

Исторический Entry snapshot и текущая геометрия — разные объекты.

Для owner-approved L5-3 Strategy текущая L5-3 после confirmed fill является
Exit-side геометрией. Entry один раз передаёт initial protection, рассчитанную
из Entry snapshot; последующие изменения L5-3 не дают Entry права менять TP/SL.
Если exact ExitPlan разрешает dynamic L5-3 target, Exit Engine может заменять TP
по текущей целевой внутренней границе L5-3 (LONG — верхней, SHORT — нижней),
сохраняя Entry/Exit ownership boundary.

Если одну и ту же Strategy geometry потребляют Entry, Exit, Position Supervisor
и Analyst/replay, они обязаны использовать один versioned geometry contract:
одинаковую formula/inputs/time semantics и fingerprint либо доказанно
эквивалентную реализацию. Независимые «похожие» расчёты разных компонентов не
считаются одной геометрией.

Не требуется искусственно считать актуальную геометрию «той же неизменной
исходной зоной».

## 3.4 H3

H3 сейчас является геометрией сопровождения/research:
- 5m на 3 часах;
- 15m на 3 часах;
- совмещение двух компонентов.

H3 не является Entry condition текущей первой Strategy.

Наблюдение H3 не создаёт торгового эффекта само по себе. Чтобы H3 двигала stop,
trailing или закрывала позицию, правило должно быть явно утверждено в новой
Strategy version.

## 3.5 Initial protection и возможные Exit actions

Для SHADOW Strategy initial protection может быть disabled как inert authoring
slot. Для Strategy, которой разрешена real Exchange mutation, owner-approved
loss-containment обязателен.

`protection_policy.initial_protection` принадлежит Strategy и передаётся в
Execution при открытии. Execution обязана подтвердить фактическое состояние
защиты на Exchange (в составе opening contract либо сразу после fill — согласно
возможностям адаптера). Missing/unsupported/unconfirmed protection блокирует
real activation/dispatch или поднимает
`POSITION_WITHOUT_CONFIRMED_INITIAL_PROTECTION`; система не подставляет global
stop.

Dynamic Exit может быть disabled/экспериментальным, но это не разрешает
намеренно держать real position без loss-containment.

Readiness real Strategy требует как минимум одного доказуемого terminal
loss-containment/close path и exact protection-failure/emergency contract.
Отсутствие такого пути блокирует real activation/dispatch; SHADOW authoring от
этого требования не получает Exchange rights автоматически.

ExitPlan может разрешать:
- SET/REPLACE stop;
- SET/REPLACE TP;
- fee-aware break-even;
- trailing;
- REDUCE;
- CLOSE;
- time exit;
- zone/geometry exit;
- MAYAK/Dispatcher context-based exit;
- protection/holding;
- hedge lifecycle.

Hedge lifecycle исполним только при совместимом Exchange position-mode contract.
В текущем one-way same-symbol hedge unsupported и fail-closed.

Exit Engine не может выполнить action, отсутствующий в ExitPlan.

Никакое старое исследовательское число не является default.

## 3.6 ExitDecision и Execution

Когда условие ExitPlan выполнено, Exit Engine создаёт exact `ExitDecision`.

Принятое ExitDecision создаёт `ExitExecutionRequest` с exact
StrategyPosition/Strategy/ExitPlan lineage.

Exit Engine не мутирует Exchange напрямую.

## 3.7 Position observation

Карточка позиции должна позволять сохранять:
- fixed Entry price;
- exact Strategy binding;
- current geometry snapshots;
- geometry changes over time;
- MFE/MAE;
- protection changes;
- exact Exit decisions/execution;
- context links.

Наблюдение не равно торговому решению.

# 4. EXECUTION — техническое исполнение

## 4.1 Назначение

Execution — техническая граница между уже принятым торговым решением и внешней
торговой площадкой.

## 4.2 Вход Execution

Execution получает typed `ExecutionRequest`.

Логически различаются:
- `EntryExecutionRequest` — от принятого EntryDecision;
- `ExitExecutionRequest` — от принятого ExitDecision.

Entry request несёт signal/attempt/EntryDecision/EntryPlan lineage.

Exit request несёт StrategyPosition/ExitDecision/ExitPlan lineage.

Оба несут exact strategy_id/version/fingerprint, symbol/direction и необходимые
Strategy-owned execution/protection параметры.

## 4.3 Запрет собственной торговой логики

Execution:
- не выбирает Strategy;
- не меняет Entry formula;
- не вычисляет H9/H3 как собственную policy;
- не подставляет 130 bars/9h/3h/stop/leverage/TTL как глобальный trading default;
- не переоценивает MAYAK/Dispatcher;
- не решает, что LONG лучше SHORT.

Если нужная Strategy-owned policy отсутствует или unsupported — fail-closed.

## 4.4 Обязанности Execution

Execution отвечает за:
- validation exact identities/fingerprints;
- exchange adapter;
- order preparation/submission;
- idempotency;
- client/exchange IDs;
- fill truth;
- fees/slippage, где они измеримы;
- initial protection;
- retries без двойной мутации;
- reconciliation;
- durable handoff;
- recovery after restart;
- technical fail-closed.

## 4.5 Exchange-agnostic

Bybit является текущим подключённым провайдером.

Архитектура Execution должна позволять другие биржевые адаптеры без изменения
Strategy/Entry semantics.

## 4.6 Operational safety

Неизвестная позиция, stale private state, потеря reconciliation, неизвестный
fill/qty/protection или owner kill имеют право технически остановить mutation.

`EMERGENCY_CLOSE` является execution capability, а не самостоятельной policy.
Автоматическое emergency action допустимо только по exact owner-approved
`lifecycle_policy.emergency_policy`/protection-failure contract. Он задаёт
разрешённый action, fault/trigger, time/freshness condition и reconciliation.
Без такого contract Lifecycle Supervisor только поднимает fault/fail-closed и
не изобретает close.

`owner kill` не равен автоматическому close: он действует только в объёме
своего owner-approved control contract.

Это operational safety, а не новая оценка рынка.

## 4.7 LIVE-arm readiness

Переход SHADOW -> LIVE EQUIVALENCE -> MICRO_LIVE -> LIVE не происходит
автоматически после deploy.

До owner-approved real arm должны быть доказаны минимум:

```text
CANON_CURRENT=PASS
REMOTE_COMMIT_VERIFIED=PASS
SOURCE_LIVE_IDENTITY=PASS
TESTS=PASS
LIVE_EQUIVALENCE=PASS

EXCHANGE_ACCOUNT_IDENTITY=PASS
POSITION_MODE_FRESH=PASS
POSITION_IDX_EXPECTED=PASS
PHYSICAL_SLOT_CLAIM_CONTRACT=PASS
CAPITAL_RESERVATION_CONTRACT=PASS

EXACT_STRATEGY_ACTIVATION=PASS
ENTRY_PLAN_EXECUTABLE=PASS
EXIT_PLAN_EXECUTABLE=PASS
INITIAL_PROTECTION_EXECUTABLE=PASS
TERMINAL_LOSS_CONTAINMENT_PATH=PASS
EMERGENCY_POLICY_SUPPORTED=PASS

LIFECYCLE_SUPERVISOR_BEHAVIOR=PASS
CRITICAL_FAULT_DELIVERY=PASS
RECONCILIATION_PATH=PASS

MAINNET_GATE_EXPLICIT_OWNER_APPROVAL=PASS
MICRO_LIVE_LIMITS=PASS
ROLLBACK_OR_KILL_PATH=PASS
```

UNKNOWN/STALE/NOT CHECKED HERE по обязательному пункту означает NOT_READY_FOR_LIVE.
MICRO_LIVE имеет отдельный лимит риска/капитала/символов и не является
синонимом полного LIVE.

OWNER DECISION 2026-10-04 — scoped exception только для текущего R1 MICRO_LIVE:
для exact cohort `r1_aptusdt / r1_injusdt / r1_dotusdt / r1_ltcusdt / r1_arbusdt`,
`requested_amount=10 USDT` на Strategy, `leverage=1x`, owner явно разрешил
real arm при `critical_delivery.configured=false`. Поэтому именно для этого
MICRO_LIVE `CRITICAL_FAULT_DELIVERY` не является blocking pre-arm check и
фиксируется как `OWNER_WAIVED_FOR_R1_MICRO_LIVE`, а не как ложный `PASS`.
Durable fault/delivery/retry/ack/escalation contract остаётся обязательным и
не отключается. Исключение не распространяется на другую Strategy, иной cohort,
увеличение лимитов или полный LIVE; для них применяется обычный checklist выше.

## 4.8 Owner-controlled real execution smoke-test

OWNER DECISION 2026-10-06: для проверки самого production execution transport
разрешён отдельный **owner-controlled real smoke-test**, который не является
StrategySignal / EntryDecision / EntryExecutionRequest и не подменяет R1.

Exact current contract:

```text
trigger = only explicit owner click in authenticated Dashboard
scope = symbol from current ACTIVE R1 MICRO_LIVE cohort
direction = LONG or SHORT selected by owner
stake = 10 USDT
leverage = 1x
entry = immediate MARKET
initial stop = -0.5% from actual fill
take profit = +0.5% from actual fill
position mode = ONE_WAY / positionIdx=0
transport = production private runtime -> Bybit
```

The smoke-test is an operator control-plane command. It MUST NOT:
- be generated autonomously;
- forge StrategySignal/EntryDecision/ExecutionRequest lineage;
- enter R1 performance/research statistics as a Strategy trade;
- bypass a closed global mainnet gate, current active MICRO_LIVE symbol scope,
  occupied Exchange position/order, or stale runtime readiness.

Its purpose is only to prove the downstream Exchange mutation/fill/protection
path under explicit owner control. Any later use as Strategy behavior requires
normal Strategy canon/versioning.

# 5. Сквозной handoff

Единственное каноническое определение обязательной lifecycle-chain находится в
docs/CRIPTA_ARCHITECTURE_RULES_RU_*.md §9.1. Этот документ её не дублирует.

Trading contour обязан сохранять exact durable lineage, включая, где применимо:
strategy_activation_id, Strategy/EntryPlan/ExitPlan fingerprints, signal_id,
strategy_attempt_id, position_mode_state_ref, exchange_position_slot_claim_id,
exchange_position_key, capital_reservation_id, decision/request IDs,
client/exchange order IDs, fill/execution IDs, strategy_position_id,
Exit claim/heartbeat и final close/economics refs.

Entry/Exit/Execution не должны молча подменять потерянный handoff новой
торговой логикой.