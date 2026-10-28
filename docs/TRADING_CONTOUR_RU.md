# CRIPTA — торговый контур: STRATEGY / ENTRY / EXIT / EXECUTION

**Версия:** 3.2
**Дата:** 2026-10-28
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

## 1.10 R1 — historical implementation candidate (RETIRED 2026-10-26)

**CURRENT OWNER DECISION 2026-10-26:** R1 и конкретная `r1_five_symbols` StrategyActivation более не действуют как текущая торговая Strategy. Владелец явно снял R1 с дальнейшей проверки Universal Entry; нельзя включать или копировать её Entry/Exit policy, параметры, one-account owner-arm или старые MICRO_LIVE исключения в новую Strategy без нового исследования, отдельного решения и версионирования. Ранее опубликованные ниже R1 параметры сохраняются **исключительно как исторический контракт и evidence**, не как активные defaults или разрешение на LIVE. Производственный R1 был деактивирован штатным CAS-контролем: `enabled=false`, `disabled_at IS NOT NULL`, наблюдатель перешёл в IDLE. Дальнейшая цель — отдельно проверить MAIN/SUB на будущей явно утверждённой Strategy и universal Entry/Exit, сохранив все safety gates. 

**OWNER DECISION 2026-10-27 — reversible R1 service retirement:**
while the R1 Strategy remains retired, its old `cripta-exit-runtime.service`
(V36.1, legacy MAIN BE/trailing) is to be **stopped and disabled from autostart**,
not deleted from source or database. Current production check found no
open legacy Exchange positions or queued/running old Exit commands, and
the systemd stop/disable was verified. Retain its code, unit definition and
historical observations for 1–2 release cycles / approximately a week of
stability; physical cleanup requires a later separate owner decision and
release. The already-disabled `cripta-r1-reverse-worker.service` remains
inactive. New Universal ExitPlan execution must never fall back to historical
R1 Exit rules. This does not authorize disabling canonical Position Supervisor,
Lifecycle Supervisor or account-safety coverage: those are still required by
ARCH §9 and its account-scope replacement checks.

HISTORICAL OWNER DECISION 2026-10-04: `R1` была label исследованной L5-3 Strategy Candidate, но ниже зафиксирован exact implementation target, разрешённый для реализации и SHADOW/test validation. Это всё ещё не production Strategy ID и не разрешение MICRO_LIVE/LIVE.

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
  pending Entry is cancelled/skipped when its six-state L5-3 structural extrema change, R1 structural rule becomes invalid, the opposite target is reached before fill, or an opposite qualified signal supersedes it; ATR200-only inner-boundary drift does not cancel the resting order

OCCUPANCY / PING-PONG
  same-symbol ONE_WAY ownership remains mandatory
  while occupied, same-side qualified Entry is ignored
  first qualified opposite Entry keeps baseline hard-flip semantics:
    immediate taker close of current position
    immediate taker open of the opposite position
  this hard-flip path does not receive maker-delay optimization

EXIT
  dynamic target = opposite RAW structural L5-3 extremum (LONG max(high36), SHORT min(low36)); ATR-free Exit price
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
Pending Entry живёт только пока структурный R1 signal остаётся действительным и
должен быть отменён при любом из условий из §1.10: six-state L5-3 structural extrema changed,
R1 structural rule invalidated, opposite target reached before fill, opposite qualified signal
superseded. ATR200 влияет на ширину зоны, но не является самостоятельным сигналом отмены.
Сдвиг внутренней вычисленной границы из-за ATR200 не переставляет уже отправленный
PostOnly LIMIT: сохраняются начальная цена и структурные экстремумы сигнала. Existing generic implementation requirement
`entry_limit_ttl_seconds > 0` therefore cannot be silently reused for R1;
implementation must support signal-validity lifetime without inventing an
untested timeout. No timeout-to-taker fallback is allowed.

Cancellation audit must preserve the actual Strategy-owned cause. In particular,
an R1 `SIGNAL_VALIDITY` cancellation such as `R1_EXACT_ENTRY_LEVEL_CHANGED`,
`R1_SIGNAL_RULE_INVALIDATED` or `R1_OPPOSITE_TARGET_REACHED_BEFORE_FILL` must
not be recorded as a TTL expiration. Once Exchange reconciliation proves
`Cancelled + zero fill`, reservation/slot/request release evidence uses the
generic zero-fill cancellation class plus the exact causal reason.

OWNER DECISION 2026-10-19 (separation of geometry ownership): Entry/H9 uses ATR200-inclusive inner-zone geometry for admission, overlap and depth; Exit TP uses the opposite raw structural extreme of the same 36 fully closed 5m candles without ATR200. Changes in ATR alone never move an open TP and never increment the structural TP-move counter. For LONG this Exit target is max(high36), for SHORT min(low36). This decision supersedes older R1 descriptions linking Exit TP to ATR-shifted inner boundary, but does not modify Entry geometry, Entry price, H9 alignment, slot ownership, or hard-stop protections. The 20/30/40/50 structural-shift closures remain experimental replay scenarios, not LIVE policy.

R1 ExitPlan:

```text
normal target:
  opposite RAW structural L5-3 extremum (LONG max(high36), SHORT min(low36)), without ATR200

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

OWNER DECISION 2026-10-07 clarifies mandatory **Entry-time exchange
protection** for R1. At every real R1 Entry, Execution must submit protection
with the opening order itself:

```text
initial SL = catastrophic stop at -10.0% from the Entry execution anchor
initial TP = exact RAW opposite L5-3 structural extremum, independent of ATR200

LONG  initial TP = max(high36) of fully closed 5m bars
SHORT initial TP = min(low36) of fully closed 5m bars
source fact      = fact.r1_opposite_inner_target
```

The initial TP is **not** a fixed `+N%` target. It is the causal
opposite structural extremum of the same causal 36 fully closed 5m bars;
ATR200 remains available for Entry alignment/overlap/depth but does not enter the Exit TP price. Its purpose at Entry is exchange-resident
autonomous protection: if CRIPTA loses server/network/runtime immediately
after fill, Bybit must already hold both the catastrophic SL and the
opposite-zone TP.

After fill/reconciliation:
- the catastrophic SL is re-anchored to the actual average fill when required;
- the Entry-time TP keeps the exact causal opposite-inner target price until an
  Exit decision replaces it;
- Universal Exit remains the owner of subsequent dynamic TP updates and may
  replace the resting TP only when the opposite RAW structural L5-3 extremum changes;
  ATR200-only inner-boundary drift must not change the existing TP;
- if the opposite target is reached before a pending PostOnly Entry fills, the
  pending Entry is invalidated by the existing exact signal-validity contract.

This clarification does not create a fixed percentage TP, does not make the
catastrophic stop a normal Exit rule, and does not transfer dynamic Exit
ownership to Entry/Execution.

Current corrected immutable R1 Strategy version for this contract =
`1.1-micro-live`; historical `1.0-micro-live` cards remain immutable evidence.

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

OWNER DECISION 2026-10-11: Acknowledgement and cancellation may race for the exact same Exchange order. Terminal RELEASED is valid only with durable exchange evidence: matching orderId, Cancelled with cumulative fill zero, no actual executions, and exact cancellation lineage. Unknown/partial outcomes remain RECONCILIATION_REQUIRED and fail-closed. This does not authorize repeating a mutation or changing the Entry policy.


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
по противоположному сырому структурному экстремуму L5-3 (LONG — max(high36), SHORT — min(low36)),
без ATR200 в цене TP и в условии его перестановки,
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



## 4.4.1 Read-only SIGNAL_VALIDITY unavailability for outstanding Entry — OWNER DECISION 2026-10-12

An outstanding Exchange-acknowledged PostOnly Entry remains an Exchange order. A temporary failed public ticker/kline read during periodic Strategy SIGNAL_VALIDITY revalidation is **not** an unknown Exchange mutation outcome. The verifier must report UNKNOWN/deferred, make **no cancellation or new mutation** from missing evidence, and retry only after bounded backoff. It must never infer SIGNAL_VALIDITY=true or false from absent/stale data. Report durable actionable degraded data-read evidence without manufacturing an ExchangeMutationBarrier or global mainnet disarm solely for this pre-cancel read failure.

The outstanding order must continue to be monitored by Exchange reconciliation and its Strategy lifetime contract; an unavailable read does not grant indefinite silent safety clearance. A failed/ambiguous mutation, uncertain fill, unsafe Exchange reconciliation, genuine protection/position invariant violation, or any independent global gate reason retains the existing fail-closed emergency contract. Recovery and LIVE re-arm are not implicit side effects of this change. This section does not change Strategy signals, target geometry, cancel-on-verified-invalid rules or Exchange mutation ownership.

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
PAPER_REAL_DECISION_PARITY=PASS

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
SOURCE_LIVE_IDENTITY для R1 проверяется по установленному exact торговому релизу и изменению исполняемого торгового контура, а не по документальным commit в GitHub main. Canonical path classification: DEVELOPMENT_RELEASE §26.2. Все остальные LIVE-arm gates неизменны.


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

### 4.8.1 Owner/operator `break_even` = «Безубыток» — OWNER DECISION 2026-10-11

Authenticated Dashboard command `break_even` для уже открытой REAL-позиции —
явная owner/operator control-plane protection action. Она не является новым
Strategy Exit rule и не меняет R1 Entry/ExitPlan автоматически.

Operator-facing label = **«Безубыток»**. Семантика команды:

```text
purpose = сохранить позицию открытой, но перенести stop на прибыльную сторону
          максимально близко к Entry так, чтобы покрыть ordinary close costs
NOT = защита текущего unrealized PnL
NOT = trailing / фиксация процента от текущей прибыли
```

Current R1 MICRO_LIVE calculation uses exact actual remaining Entry fee, expected
taker Exit fee and only base technical slippage reserve:

```text
base_slippage_price = max(tick_size, entry_price * 0.0002)
slippage_reserve    = base_slippage_price * remaining_qty
minimum_net_profit  = 0 USDT
stop                = fee-aware zero-net boundary, rounded by tick
                      toward the profitable side
activation          = one tick further into profit than stop
```

The owner `break_even` calculation MUST NOT include:
- current unrealized profit / current mark-to-entry profit;
- a separate `+0.01 USDT` protected-profit floor;
- recent/worst observed trigger-to-fill slippage history;
- trailing distance or any percentage of current PnL.

Therefore `+0.12…+0.13% from Entry` may be a normal current result for a
~10 USDT R1 position under the present fee/tick conditions, but this range is
**not** a hard-coded Strategy/global percentage. The exact stop is recalculated
from actual fee/qty/tick economics for each position. Exchange gap/slippage can
still produce a worse actual fill; this operator command is ordinary fee-aware
break-even, not a worst-case profit guarantee.

A resulting manual protection mutation is attributed as `OWNER_MODIFIED_STOP`.

## 4.9 PAPER / REAL execution mode — OWNER DECISION 2026-10-06

Execution mode — это выбор среды исполнения одной активной Strategy, а не
выбор другой торговой реализации.

```text
StrategyActivation OFF
  -> Strategy не формирует новые attempts

StrategyActivation ON + real execution permission OFF
  -> PAPER
  -> общий Entry/Exit lifecycle остаётся активным
  -> Exchange open/close mutation запрещена
  -> execution/fill/position lifecycle симулируется

StrategyActivation ON + real execution permission ON
  -> REAL candidate
  -> тот же StrategySignal / attempt / mode-neutral EntryExecutionIntent
  -> mandatory real admission / gates
  -> real EntryExecutionRequest только после ACCEPTED
  -> Exchange mutation/reconciliation
```

`mainnet gate` остаётся дополнительным global safety barrier и не превращает
PAPER в отдельную Strategy.

Для одного `strategy_attempt` PAPER и REAL execution взаимоисключающие. При
REAL mode admission failure не разрешается открывать PAPER-position как
fallback. Operator/read-model обязан показывать exact real block reason.

`EntryExecutionIntent` — mode-neutral представление уже рассчитанных
Strategy-owned Entry execution semantics до выбора adapter. Оно несёт exact
Strategy/Plan lineage, direction, reference/limit policy, validity и
protection/lifecycle inputs. REAL `EntryExecutionRequest` создаётся из этого же
intent только после real admission; PAPER simulation adapter потребляет этот же
intent без подделки real admission entities.

Для Exit обе среды обязаны использовать один exact ExitPlan и Universal Exit
decision semantics. PAPER отличается только simulated execution/fill truth;
REAL — Exchange execution/reconciliation truth. Любое result-affecting
расхождение Entry/Exit policy между PAPER и REAL является defect.

Перед включением real execution exact Strategy version должна иметь
`PAPER_REAL_DECISION_PARITY=PASS`. Этот gate не заменяет account/slot/capital/
position-mode/mainnet safety checks.

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

## 1.10.2 R1 resting Entry — восстановление структурной семантики (OWNER DECISION 2026-10-08)

R1 Entry определяется квалифицированной структурой L5-3. При создании PostOnly
LIMIT фиксируются `range_low`, `range_high` текущей подтверждённой зоны,
исходная limit-цена и шесть стабильных состояний. В течение ожидания Entry
перепроверяет current stable zone из достаточного причинного окна, сравнивает
её структурные экстремумы с исходными и проверяет остальные исходные условия.
Чистый пересчёт ATR200 без изменения структурных экстремумов не создаёт
`R1_EXACT_ENTRY_LEVEL_CHANGED`, не отменяет и не переставляет лимит.
ATR200 используется в расчёте ширины зоны; отдельного ATR-сигнала отмены нет.
Изменение structural extrema, потеря стабильности/правила диапазона,
противоположная цель до fill и superseding opposite signal сохраняют свои
исходные cancellation semantics. Никаких tolerance thresholds не вводится.
Это восстановление первоначального operator intent, не автоматическое разрешение
нового LIVE-arm; старые immutable event/evidence записи не переписываются.

## 6. Normal restart recovery — OWNER DECISION 2026-10-08

Execution/Lifecycle reconciles fresh complete Exchange snapshots with durable Entry/Exit lineage, orders, execution IDs and capital reservations. Preserve local trade history; restore exact results after fees where exchange evidence suffices. If current exposure is absent but exit evidence incomplete, mark historical outcome UNKNOWN rather than inventing P&L or holding all independent Strategies hostage. Restrict unresolved mutation only within its exact affected scope. No queue replay or automatic LIVE-arm from restart alone.

## 4.10 Owner decision 2026-10-09 — R1 re-arm without flat Exchange inventory

For the current exact R1 MICRO_LIVE cohort, re-arm authorizes **future NEW Entry** only.
Existing Exchange positions, protective orders and lifecycle commands are not
an independent veto on new-entry re-arm, and must never be closed/cancelled
to satisfy a flat-account precondition. The PREARM check `exchange is not flat`
is removed. Existing position and order state is still retrieved authoritatively,
with fresh successful Exchange reconciliation, authenticated Bybit connection,
account identity/capital, exact release and strategy activation, position mode,
Entry/Exit plan loading and live infrastructure checks retained.
Individual new Entry requests still enforce physical slot ownership, exposure,
capital reservation, idempotence and initial protection. Existing Exit/position
supervision remains independent; unresolved safety faults are not waived by
this policy. No LIVE re-arm is a side effect of deploy.

## 4.10.1 Owner decision 2026-10-21 — historical LIVE-arm retirement correction

The 2026-10-15 requirement to wait for a flat Exchange account before closing
a prior-release LIVE-arm row is **SUPERSEDED**. It conflicted with §4.10, §6,
ARCH §46 and DEVELOPMENT_RELEASE §44–46: existing exposure never prevents
deploy, restart or release supersession. A prior-release arm is authorization
for **new Entry**, not ownership of the position or its Exit.

After verified LOADED_COMMIT changes, former-release sessions cannot authorize
new Entry. With global mainnet new-Entry gate OFF, independent Lifecycle/control
retires their ACTIVE rows with durable exact-release audit, whether Exchange
positions/orders, local commands/reservations/claims or historical incomplete
bindings exist or not. This retirement does not inspect, modify, close or
reassign those resources and never implies that Exchange inventory is flat.
Existing positions remain protected by already placed Exchange safeguards
subject to Exchange risks, and the new Execution/Lifecycle/Universal Exit
runtime must reconcile and resume their exact authorized ExitPlan, including
when Exit implementation changed without retroactively changing Strategy
policy. Recovery and fault checks continue independently from installer.
No automatic mainnet enablement, execution permission or OWNER ARM follows;
new Entry still requires fresh exact-account/Strategy/release checks and
explicit owner approval. DEVELOPMENT_RELEASE §46 owns detailed procedure.

## 4.11 Owner decision 2026-10-09 — R1 dynamic L5-3 Exit TP structural changes and Exchange price steps

Only a structural change of the **opposite working-range extremum** qualifies a
new R1 dynamic SET_TP Exit decision: `range_high` for LONG and `range_low`
for SHORT. A standalone ATR200 change with unchanged applicable extremum
does not trigger a new TP decision. The unchanged rolling 36 CLOSED 5m candles
remain authoritative; when an old candle leaves the rolling window and
changes the relevant extremum, that is a genuine structural change and must
be processed. No hysteresis threshold, time cooldown or candle-outfall
exclusion is introduced. Neither Entry policy nor L5-3 geometry is changed.

When a genuine structural change occurs, the TP objective continues to use
the original opposite zone **INNER** boundary: LONG `range_high - 0.5 * ATR200`;
SHORT `range_low + 0.5 * ATR200`. The ATR value is the one current at that
structural event, not an independently actionable trigger.

The precise Strategy geometry remains unrounded. Exchange Execution must
fetch the exact instrument's current `priceFilter.tickSize` and
`lotSizeFilter.qtyStep` from Bybit instrument metadata, fail closed if
invalid/missing, and normalize only the executable TP price. For a LONG
position's sell TP the floor tick is used; for a SHORT position's buy TP the
ceiling tick is used (toward the market, never further than the objective).
The calculated objective, normalized price and Exchange-acknowledged order
state must remain distinguishable in lineage; an unchanged confirmed
effective price/quantity must not cause a redundant Bybit amendment/order.

This decision concerns existing R1 dynamic SET_TP only. Stop Loss rounding,
other Strategies, changes to Entry rules, new trailing/threshold policy,
and proactive LIVE re-arm are not authorized by this section.

# 7. Multi-account / multi-venue execution identity — OWNER DECISION 2026-10-16

ARCH §49 owns the upper identity/cardinality model. This section defines how the
trading contour consumes it.

## 7.1 Strategy is account-independent policy

StrategyCard / EntryPlan / ExitPlan do not hard-code a physical TradingAccount.
StrategyActivation selects execution environment separately from the immutable
trading policy.

For REAL execution, every accepted strategy_attempt must resolve through an
active StrategyExecutionBinding to one exact ExecutionLeg and TradingAccount
before capital reservation / physical-slot admission. Missing, stale,
overlapping or ambiguous binding is fail-closed and cannot fall back to a
default account.

## 7.2 REAL admission is TradingAccount-scoped

AccountStateGeneration, available capital, position-mode evidence, physical
slot, hot/private inventory, protection and reconciliation must all belong to
the same exact trading_account_id as the ExecutionLeg.

A successful check against another account on the same Venue does not satisfy
admission. Likewise public Bybit market context is not private account evidence.

The current one-way physical slot remains account-scoped. Multiple
TradingAccounts may legitimately hold the same InstrumentIdentity and
positionIdx concurrently without ownership conflict.

## 7.3 Dedicated Strategy account isolation

Normal REAL deployment uses DEDICATED_STRATEGY isolation: one TradingAccount
serves at most one independent REAL StrategyActivation at a time. This gives
separate capital, loss, profit, positions and recovery evidence.

One StrategyActivation may use more than one TradingAccount only through
explicitly named ExecutionLegs. Multi-leg atomicity/hedge/arbitrage semantics
are Strategy-owned and require their own approved Strategy contract; the mere
existence of multiple bindings does not create an arbitrage policy.

Sharing one TradingAccount across independent REAL Strategies, internal netting
or cross-Strategy capital pooling remains forbidden until separately approved.

### 7.3.1 Strategy fan-out, capacity and protected opening — OWNER DECISION 2026-10-20

A saved copy of StrategyCard is an independent Strategy ID and configuration,
not a runtime child of its source. Strategy-owned configuration sets the coin
universe, number of concurrent instruments/positions, stake, leverage and
one-way/hedge permission; merely changing any one setting never changes
the common Universal Entry/Exit Engine.

For each accepted Strategy signal, the approved EntryPlan may yield one
independent account-scoped ExecutionRequest for each eligible authorized
StrategyExecutionBinding. Each account must pass its own capital, exposure,
slot ownership, account position mode, instrument support, and LIVE-arm checks.
There is no implicit cross-account atomicity. Per-account partial acceptance,
rejection, recovery and Exit must remain attributable and auditable. One
TradingAccount cannot be concurrently bound to two independent REAL Strategy
activations; one Strategy may use any number of separately proven accounts.

Capacity is Strategy policy, not a hard-coded one-position-per-Strategy or
one-position-per-connection rule. In one-way, independently opening the same
occupied physical account/instrument slot is prohibited unless a separately
approved plan explicitly manages the existing position. In hedge mode, opposite
sides are possible only if Strategy policy and exchange/account mode both
support them. An illustrative ten-instrument policy may allow ten one-way
positions, or up to twenty directional legs if both sides of all ten are
explicitly authorized and sufficient capital exists; this is not an implicit
global default.

Entry decision ownership stays with Strategy/EntryPlan. Execution implements
technical venue placement and confirms exchange-resident protective orders
according to ExitPlan; it may not declare a newly opened position safely
protected before confirmation. Fail-closed/recovery paths preserve real Exit,
reconciliation and exact physical/account ownership.

## 7.4 Execution and recovery lineage

EntryExecutionRequest / ExitExecutionRequest and durable lifecycle lineage must
carry or resolve the exact TradingAccount and ExecutionLeg used for the
mutation. Exchange recovery is performed per TradingAccount/AccountConnector and
must never reconcile one account's open orders/positions into another account.

Strategy statistics remain attribution-gated by ARCH §47. A dedicated account
makes accounting clearer but does not replace exact Strategy ownership lineage.

This revision changes no Entry threshold, Exit rule, stake, leverage, protection
or LIVE-arm policy.


## 7.5 R1 one-Strategy/five-symbol configuration — OWNER DECISION 2026-10-21

The owner clarified R1's intended identity: **one independently versioned R1
StrategyCard**, permitted symbols APTUSDT, ARBUSDT, DOTUSDT, INJUSDT, LTCUSDT;
both LONG and SHORT, one StrategyActivation and one dedicated TradingAccount
through one proven StrategyExecutionBinding. The number of instruments does not
multiply Strategy identity, StrategyActivation, account assignments or OWNER ARM.

The Materializer persists one whole-Strategy EntryPlan and the matching ExitPlan
per exact activation. The EntryPlan monitor is a read-only projection:
`EntryPlan × InstrumentIdentity × direction`; five symbols and two directions
produce ten monitored rows with common plan fingerprint, not ten autonomous
Strategies nor ten persisted EntryPlans. The Dashboard must distinguish the
Strategy, whole-plan identity, and per-direction readiness.

The five historical per-symbol R1 Strategy identities created before this owner
clarification are not rewritten, reused or silently merged. Exact historical
StrategyCard, StrategyActivation, EntryPlan, ExitPlan, fills, PAPER positions,
P&L and protective Exit ownership remain attributable to original identities.
A future transactional activation transition must first prove pending PAPER
orders and active PAPER/REAL position/Exit continuity. It must stop **new**
signals/orders on legacy activations without orphaning existing Exit. Existing
real execution permissions cannot be copied to a new Strategy or a new release.
An inert new StrategyCard alone does not prove LIVE admission readiness.

This is an architecture alignment, **not a change to R1 trading numbers**.
The source-only unified R1 constructor changes neither DB activations,
TradingAccount bindings nor mainnet gate, and does not perform OWNER ARM.
Implementation, tests, verified deploy and runtime behavior have separate
statuses in CURRENT_PROJECT_MAP.

### 7.5.1 One-Strategy R1 MICRO_LIVE loss/capital limit — OWNER DECISION 2026-10-21

The owner explicitly confirms the financial envelope for the **one**
`r1_five_symbols` Strategy / one dedicated MAINNET TradingAccount:
- exact permitted symbols: APTUSDT, ARBUSDT, DOTUSDT, INJUSDT, LTCUSDT;
  directions LONG/SHORT for monitoring and signals, ONE_WAY position mode,
  positionIdx=0; one physical open position slot per coin at a time;
- requested amount **10 USDT per position** per coin, leverage **1x**;
- total concurrent Strategy requested exposure/capital (open positions,
  reservations, pending Entry commitments as applicable) **at most 50 USDT**.
  The limit is across this one Strategy's five coin slots, NOT five separate
  StrategyActivations or five independent account authorizations;
- exchange-resident catastrophic initial SL **-10%** per executed
  position, submitted and exchange-confirmed under the existing R1
  initial-protection/lifecycle contract. This is not an ordinary Exit
  target and does not replace dynamic ExitPlan ownership.

Real Entry admission must enforce both each 10 USDT request and the
account/Strategy aggregate 50 USDT ceiling transactionally; simultaneous
pending/reserved and open capital cannot silently exceed the envelope.
This decision does not authorize an additional position in an already
occupied one-way physical slot, a second TradingAccount, higher leverage,
an altered Entry/Exit algorithm or a new default from historical research.

The historical five-Strategy R1 MICRO_LIVE cohort and its exceptional
`CRITICAL_FAULT_DELIVERY` waiver in §4.7 do **not** authorize the new
unified Strategy. Its pre-arm evidence must use one exact
StrategyCard/StrategyActivation/EntryPlan/ExitPlan, one exact PRIMARY
StrategyExecutionBinding, five instrument-scoped mode/slot proofs,
one independent aggregate capital constraint and exact release identity.
The ordinary non-waived critical delivery requirement remains in force
until a separate explicit owner decision. New Strategy policy fields
affecting results require immutable versioning and explicit activation
transition; no historical R1 rows are retroactively edited.

This is an **OWNER DECISION approving risk parameters only**. It is NOT
`MAINNET_GATE_EXPLICIT_OWNER_APPROVAL`, an OWNER ARM, a deploy instruction,
or permission to send an Exchange order. Canonical order remains:
CANON UPDATE -> IMPLEMENTATION -> TEST/SHADOW -> LIVE EQUIVALENCE ->
explicit owner-authorized MICRO_LIVE admission. Until all exact new-Strategy
gates are proved, `mainnet=0`, execution permissions OFF, no new ACTIVE
LIVE-arm session, and existing Exit/reconciliation continue independently.


## 7.6 Two independent passive P90L90 LEGACY Strategies — OWNER DECISION 2026-10-27 (Issue #243)

Owner explicitly authorizes authoring and durable registration of TWO NEW independent immutable StrategyCards based on the historically tested P90L90 PREV7D + TRAIL035 + opposite RAW L5-3 TP + SECOND48, WITHOUT any trading activation, Exchange orders or LIVE-arm. Historical five-coin results remain research evidence (RESEARCH_COMPUTE §§22.1–22.2), never prior R1 policy or live proof.

- New independent Strategy ID p90l90_legacy_main_ltc_inj, symbols LTCUSDT, INJUSDT, directions LONG and SHORT; one proposed PRIMARY dedicated binding to the proven MAIN TradingAccount.
- New independent Strategy ID p90l90_legacy_sub_dot_arb_apt, symbols DOTUSDT, ARBUSDT, APTUSDT, directions LONG and SHORT; one proposed PRIMARY dedicated binding to the proven SUB TradingAccount.
- Both have separately versioned StrategyCard, own EntryPlan and ExitPlan with exact fingerprint and independent disabled StrategyActivation. All trading policy is identical except distinct universe and identity; no runtime inheritance. TradingAccount and AccountConnector identities live solely in StrategyExecutionBinding, not StrategyCard (ARCH §49). Public Bybit MarketSource can be shared.

Exact Entry reference: from only fully closed 5m/15m states L5-3 36×5m, H9-5m 108×5m, H9-15m 36×15m; ATR Wilder 200 and 0.5×ATR inner range for working-width only, with causal warmup. LONG requires exact H9-5m low_raw == H9-15m low_raw; SHORT requires exact H9-5m high_raw == H9-15m high_raw, zero tolerance. Denominator is positive RAW_H9_UNION_SPAN = max(H9-5m high,H9-15m high) - min(H9-5m low,H9-15m low); LONG numerator L5-3 low_raw - H9 low_raw, SHORT numerator H9 high_raw - L5-3 high_raw; admissible 100×numerator/denominator inclusive [0%,15%]. Each symbol PREV7D width median from 7 prior fully complete UTC days, daily UTC refresh, ±0.5 percentage points inclusive, no partial-day/future fallback. First adjacent ordered public-trade crossing of the L5-3 RAW boundary per symbol/5m-state/side: LONG previous > low_raw >= current; SHORT previous < high_raw <= current. One independent one-way occupied slot per instrument; no STAY6, H3, OI or invented cooldown.

Exact Exit reference: catastrophe SL at −10% gross from Entry; after first favorable move >=+0.5% gross activate monotone continuous high-watermark trailing distance 0.35 percentage points (first protection +0.15% gross), never loosen, no retroactive activation-tick fill. Dynamic opposite RAW 36-closed-5m L5-3 target LONG max(high36), SHORT min(low36), only armed/replaced if prospective gross favorable >=+0.5% from Entry, recalculated on newly closed 5m state. Causal event ordering: pre-existing stop, pre-existing resting TP, marketable/updated TP, finally tighten protection effective on later native trade. No arbitrary duration cap or retroactive completion.

SECOND48 baseline: second actual Hard Stop in rolling 48h pauses new Entry for 48h, without closing open positions. Historical baseline was ONE five-symbol portfolio.

**OWNER DECISION 2026-10-28 — Strategy-scoped configurable SECOND48 (Issue #243).** SECOND48 is a **Strategy lifecycle policy**, not a global exchange/account timeout. Each immutable StrategyCard/StrategyVersion explicitly defines `second_hardstop_embargo` with `enabled`, `hardstop_count`, `rolling_hours`, `new_entry_pause_hours`, `close_existing_positions=false`, and `domain=STRATEGY_VERSION`. For current proposed LEGACY profiles the values are enabled, 2, 48, 48 and no forced closure; their per-strategy event/pause state must be independent, shared by LONG and SHORT and all coins *within the same exact Strategy version*. Therefore MAIN LTC/INJ and SUB DOT/ARB/APT NEVER increment or reset each other's counters. Count only realized catastrophic `HARD_STOP` Exit events, not protected/trailing stops, cancellations or modeled adverse price alone. After threshold in the rolling UTC window, forbid only subsequent new Entry for configured duration; existing positions remain under Exit supervision. The window and pause duration are separately configurable positive parameters, and disabling the rule must be explicit. Restart must recover the exact Strategy ID/version/fingerprint, hard-stop source event identities and pause deadline durably; no account-inferred or process-global state. Configuration changes require a **new immutable Strategy version with a new fingerprint** and approved StrategyActivation/plan lifecycle; no in-place rewrite of two already registered passive 2026-10-27 cards or retrospective reinterpretation of historical ledgers. Existing stored `cross_strategy_domain=UNRESOLVED_OWNER_CONTRACT` is historical authoring state and shall remain NOT_READY until superseded by fully validated versions. No global five-coin historical P90L90 SECOND48 result is automatically transferable to two independently configured portfolios. Independent replay/after-fee evidence must demonstrate revised per-Strategy economics before enabling SHADOW; REAL continues to require all other gates.

**PAPER-only continuation 2026-10-28 — no-order P90L90 native-trade proxy (Issue #243).** Before any separately approved REAL capital/order/fill contract, the exact owner-approved two P90L90 StrategyVersion v2 may simulate a strictly **non-Exchange** PAPER position at the causally verified first adjacent Bybit `PUBLIC_TRADE` RAW-crossing price. The *research-model* notional is fixed at 10 USDT per simulated position to preserve the historical P90L90 fee/economics comparison; `schema_leverage=1` is a required database-bookkeeping denomination, **not** an approval for any leverage or Bybit stake. No `paper_orders`, `EntryExecutionRequest` for REAL, exchange order, wallet capacity reservation, Exchange fill/ack, LIVE-arm, physical slot mutation or credentials may arise from this rail. PAPER must preserve exact immutable Strategy ID/version/fingerprint, EntryPlan/ExitPlan, source publicTrade identity/sequence, dedicated TradingAccount binding, one-way position slot, after-fee modeled ledger, durable Exit checkpoint and per-Strategy SECOND48 at transaction boundaries; lifecycle fail-closed on stale/missing causal transport/geometry, ambiguity or incomplete model. The original `execution_policy.order_type=NOT_APPROVED` remains immutable in v2 and **always** disqualifies real `execution_ready`; a positive `paper_ready` and `monitor_ready` prove only the separate zero-order simulation adapter under independently checked observer continuity. Passive database registration `enabled=false` does not itself enable SHADOW. Before owner-enabled SHADOW, require matching independent two-domain research/QC, complete Entry→PAPER→Exit integration tests, exact installed release and runtime evidence. Prior v1 `UNRESOLVED_OWNER_CONTRACT` snapshots remain untouched and NOT_READY.

**Historical research evidence (NOT CANON/TRADE APPROVAL; CHECKED HERE 2026-10-28)**: full 2026-05-18 to 2026-08-16 UTC native-feed replay for two separately scoped strategies completed on actor `cripta`, job `job_260cb2bbf0dd`, 81,130,072 source trades, trade-by-trade baseline parity + ledger/source SHA-256 QC. At modeled 10 USDT per position, AFTER historical fee proxies: MAIN LTC/INJ `SECOND_48H=+5.831220832855 USDT`, SUB DOT/ARB/APT `+7.811427933175 USDT`; independent total `+13.642648766030 USDT`, versus former historical single five-coin SECOND48 `+14.087200729269 USDT`. The two numbers are different portfolio semantics. Bid/ask spread, slippage, funding and latency remain unmodeled; this validates research economics **only**, not exact new source consumer runtime parity or future profitability. Full provenance and immutable result paths/hashes: GitHub Issue #243 checkpoint 6456291425.

Execution constraints: the source research assumed immediate 10-USDT-notional public-trade-price entry only as historical proxy; it did NOT approve actual Bybit order placement, leverage, real position stake/capacity or exchange fill semantics for these two new StrategyCards. Missing exact capital/order/embargo consumer policy must remain NOT_READY, not be filled by historical R1 defaults. Authoring a passive StrategyCard is distinct from enabling real trading; however no executable EntryPlan/ExitPlan equivalence claim may be made until exact new RAW-H9, PREV7D, TRAIL035, RAW L5-3 TP and SECOND48 consumers and tests exist.

Allowed safe target: immutable cards and plan snapshots after exact materialization; disabled activations; audited INACTIVE/non-effective account assignment using an approved writer/actor and verified identity; zero new enabled execution permissions, mainnet off, zero live arms, no Exchange mutation. A pre-existing historical binding is not erased or silently reassigned. Signed MAIN provenance, account-specific writers, lifecycle/Exit proof and eventual separately authorized owner-armed MICRO_LIVE remain independent future gates.

Status of this section: owner intention accepted; Canon publication requires ordinary GitHub review. This document alone is NOT implementation, DB-registration evidence, deployment or runtime verification.


### 7.6.1 P90L90 PAPER lineage across code-only releases — OWNER DECISION 2026-10-28 (Issue #243)

The owner approves retaining the five-symbol original P90L90 5m/15m candle archive and causal Entry/transport restart checkpoints across a verified code-only bugfix/release, **without copying, rewriting or reseeding historical facts**. Market-data lineage must not be equated to `INSTALLED_COMMIT` / `LOADED_COMMIT`. The storage/compatibility owner is ARCH §48.2 and glossary §38; this clause owns only the PAPER Strategy gate.

P90L90 activation and the live Observer must prove a versioned compatibility attestation for the exact original causal dataset, all five symbols, their immutable StrategyCard/EntryPlan/ExitPlan fingerprints and independent MAIN/SUB SECOND48 state, plus exact loaded executable release. Only then can the existing 10-USDT-model, zero-order PAPER adapter be activated; historical records retain actual origin/source identity, and newly committed facts record their own producer release. No `source_commit=LOADED_COMMIT` equality shortcut, fabricated native `publicTrade` cursor, legacy R1 inheritance, forced PAPER position, REAL order, execution grant or LIVE-arm. A missing/inconsistent attestation blocks activation, not a reason to reset the durable seed.




### 7.6.3 Два P90L90 REAL MICRO_LIVE профиля — OWNER DECISION 2026-10-28 (Issue #243)

**Owner exact instruction:** «Подтверждаю, запускай уже быстрее» в ответ на отдельное предложение следующих параметров для **двух современных P90L90**, MAIN LTC/INJ и SUB DOT/ARB/APT: **10 USDT на одну позицию, isolated 1×, MARKET вход, exchange-resident catastrophic SL −10%, не более одной позиции на монету, одновременный лимит 20 USDT MAIN / 30 USDT SUB**. Это approval immutable **REAL execution contract** для новой StrategyVersion, НЕ право ретроспективно переписать PAPER-v2, закрыть preexisting позиции, изменить Exchange gate или создать приказ без independent readiness+owner-arm. Новая версия materialized StrategyCard→EntryPlan/ExitPlan обязательна.

**Unchanged Entry/Exit/lifecycle:** обе новые реальные StrategyVersion наследуют только точно утверждённую текущую P90L90 policy §7.6 — H9/L5-3 exact RAW, ATR200, PREV7D, first ordered native trade crossing, continuous after-fill trailing at +0.5% with distance 0.35 pp, dynamic opposite RAW L5-3 target, SECOND48 per immutable StrategyVersion. Никаких новых H3/OI/cooldown/Entry gap/TP thresholds. В расчетах после комиссий фактические Exchange execution/fill/fee/funding, а не publicTrade research-proxy. Состояние позиций и эмбарго каждой версии отдельно, MAIN не изменяет SUB.

**Real capital + order contract by exact StrategyVersion**:
- MAIN: symbols LTCUSDT, INJUSDT; independent MAIN TradingAccount binding; maximum **2 concurrent per-symbol slots**, total reserved+pending+open requested capital **20 USDT**.
- SUB: symbols DOTUSDT, ARBUSDT, APTUSDT; independent SUB TradingAccount binding; maximum **3 concurrent per-symbol slots**, total reserved+pending+open requested capital **30 USDT**.
- Per new entry `requested_amount=10 USDT` **at 1× isolated**, interpreted as 10 USDT targeted position notional/initial-margin budget before fees (at 1× these are nominally equivalent). Account capacity must include actual fees, rounding and existing/pending reservations; cannot exceed approved ceilings, cannot borrow other-account capacity, and cannot silently increase position to meet an exchange minimum. If symbol's Bybit minimum lot/tick/notional prevents compliant 10 USDT, reject Entry fail-closed rather than upsize. Exact normalized quantity depends on current Bybit instrument filters; never derive from paper proxy.
- `entry.order_type=MARKET` only for a **new** causally verified signal after current live publicTrade epoch, under exchange-one-way `positionIdx=0`, `marginMode=ISOLATED`, `leverage=1x`. No hedge, duplicate reservation, on-behalf account substitution or order sent from old PAPER path. Use actual Exchange orderAck/executionId/partial-fill and idempotent command ledger; every attempt and reservation belongs to exact StrategyVersion+TradingAccount/AccountConnector.
- `initial_protection` = Exchange-resident SL at **10% gross adverse price from actual position average executed Entry price**; LONG `entry_fill_avg × 0.90`, SHORT `entry_fill_avg × 1.10`, rounded in safe direction by current exchange tick contract, with native conditional trigger method only after exact venue support validated. Ack of entry order ≠ filled position; ack of stop request ≠ confirmed active stop. All newly filled exposure must be protected or immediately subject to fail-closed emergency reconciliation; partial fill/protection race, reduce-only close, position quantity and after-fill TP/Exit updates require explicit tests. Do not use simulation trade price to calculate real SL.
- Existing ExitPlan owns trailing/TP updates after actual fills; Exchange stop stays protected without momentary unprotected exposure, no replacement that widens stop. First real trade only when native-mode, signed private order/fill state, fresh publicTrade cursor, account-scoped Position Supervisor/Lifecycle worker, protection status and emergency fault delivery are independently observed and attested. The installed service is not proof of behavior.
- Capital ceilings are **per Strategy + exact dedicated TradingAccount**, not an aggregate across both accounts; slots account for queued, in-flight, reserved and confirmed physical exposure. On restart recover pending orders, exchange and local position state, current Exchange SL, and SECOND48 before any new Entry. Missing/reconnecting private WS => mainnet/account gate OFF; no speculative orders or fresh Entry after disconnection.

**Release / live-arm distinction:** `OWNER_REAL_CAPITAL_CONTRACT=APPROVED`, `CANON_UPDATE_REQUIRED=YES` and `IMPLEMENTATION_AND_TEST_REQUIRED=YES`. The owner's request to proceed is not evidence of `READY_FOR_OWNER_ARM`. Exact approved release, loaded process identity, signed MAIN+SUB account safety snapshots, position/protection reconciliation, private WS continuity, deterministic order/exit tests and explicit target release/account scope arm checks remain separate mandatory gates under TC §4.7/§4.9, ARCH §7.2 and DEV §46. Until all pass `REAL_READY=NO`, `LIVE_ARM=OFF`; no startup/registration may automatically set `StrategyActivation.enabled`, permissions or Exchange gate. No real diagnostic smoke unless independently owner-triggered under TC §4.8.

### 7.6.2 Общие closed candles и P90L90 restart — OWNER DECISION 2026-10-28

Физический владелец и единое местоположение OHLC — `ARCH §48.4`,
`analytics.closed_candles`; не добавлять новые per-Strategy/per-TradingAccount
closed-candle writers. При startup/restart без ожидания живой проторговки
запрашивать исторические закрытые 5m/15m по точному утверждённому источнику
и полному `StrategyCard / EntryPlan / ExitPlan`. H9 требует 108 закрытых 5m
и 36 закрытых 15m свечей (9h), L5-3 — 36 закрытых 5m (3h).
Но неизменяемый P90L90 v2 дополнительно требует ATR200 и семи **полностью
завершённых предыдущих UTC суток** для PREV7D. Только 9h для v2 недостаточно;
короче менять можно лишь новой StrategyVersion после исследований, а не
подменой source window.

Исторические OHLC заполняют геометрию и gap в свечах, не создают пропущенных
TradeCursor или Entry. Old P90 seed `source_commit=ecde511...` и
пять checkpoint из §7.6.1 остаются неизменяемой доказательной историей.
Только после независимого контроля отсутствующих позиций/pending и доказанного
начала нового native publicTrade epoch можно разрешать PAPER-сигналы на
**новых** сделках. Нельзя просто перезаписать старый generation=0 checkpoint
или обходить модель source continuity. Сохранённая открытая позиция
обязательно проходит собственный Exit/lifecycle reconciliation.
