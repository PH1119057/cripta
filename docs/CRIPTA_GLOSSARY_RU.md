# CRIPTA — канонический словарь

**Версия:** 1.6
**Дата:** 2026-09-25
**Статус:** обязательный канонический терминологический контракт

Если термин владельца отсутствует здесь или допускает несколько трактовок,
исполнитель обязан уточнить смысл до разработки, исследования или расчёта.

# 1. Strategy

**Strategy / Стратегия** — утверждённый владельцем торговый смысл конкретного
способа торговли и его lifecycle внутри Strategy layer.

**StrategyCard** — пассивная неизменяемая версионированная карточка утверждённой
Strategy. Она не наблюдает рынок и сама не создаёт сигнал во времени.

**Strategy Materializer** — компонент внутри Strategy layer, который
детерминированно преобразует exact StrategyCard/version в immutable EntryPlan и
ExitPlan и публикует их для универсальных Engines. Он не является отдельным
верхнеуровневым слоем и не добавляет торговую policy от себя.

**Strategy Candidate / Strategy Draft / кандидат / черновик Strategy** —
изменяемое исследовательское описание предполагаемого способа торговли до
утверждения владельцем. Candidate/Draft не является StrategyCard, не получает
StrategyActivation и не имеет live trading rights. Внутри Candidate/Draft можно
изменять параметры без создания новой production Strategy version. После
явного решения владельца утверждённый снимок Candidate/Draft становится новой
immutable StrategyCard/version.

**StrategyActivation** — отдельное состояние включена/выключена. Не изменяет
StrategyCard.

**Strategy settings / настройки Strategy** — все decision/execution-affecting
параметры конкретной Strategy, разложенные по явным policy-блокам StrategyCard
(`entry_policy`, `touch_policy`, `capital_policy`, `protection_policy`,
`exit_policy`, `lifecycle_policy` и context policies). Это не глобальные
defaults и не отдельный скрытый runtime-конфиг.

**Базовая защитная рамка / initial protection** — Strategy-owned защита,
передаваемая в Execution при открытии позиции. Она может включать hard stop и
верхнюю защитную границу/TP. Наличие такой рамки не означает, что динамический
Exit уже исследован или утверждён.

**Экспериментальная Strategy version** — owner-approved immutable снимок
Strategy Candidate/Draft для конкретного воспроизводимого теста/shadow/
MICRO_LIVE прохода. Неустойчивость исследуемых параметров не разрешает менять
такую карточку на месте: следующий вариант получает новую Strategy version.
Параметры экспериментальной версии не становятся глобальными defaults.

**EntryPlan / ExitPlan** — материализованные неизменяемые планы exact Strategy
version для универсальных Entry/Exit Engines.

**ActivePlanRegistry** — durable/read-model registry опубликованных exact
EntryPlan/ExitPlan, доступных универсальным Engines. Registry не владеет
торговой policy и не изменяет планы.

**StrategyPosition** — логическая позиция exact Strategy version после
confirmed open fill, связанная с Strategy/EntryPlan/ExitPlan lineage и
фактическими exchange/order/fill refs.

# 2. Entry

**Entry / Вход** — специализированная часть Strategy и универсальный механизм
поиска момента выполнения EntryPlan.

**Entry Engine** — активный универсальный наблюдатель/исполнитель EntryPlan.
Получает рыночные факты и активные планы, проверяет совпадение условий и создаёт
StrategySignal.

**Entry zone / зона входа** — общее понятие области, в которой конкретная
Strategy допускает/ищет вход. Не является одной универсальной формулой.

**Entry point / Entry price / точка входа** — конкретная цена фактического или
моделируемого входа. После состоявшегося fill фактическая Entry price является
неизменяемым историческим фактом.

**Exit Engine** — активный универсальный исполнитель ExitPlan. Получает
StrategyPosition + exact ExitPlan, наблюдает разрешённые планом факты и при
выполнении условия создаёт ExitDecision.

**ExitDecision** — формализованное решение Exit Engine выполнить конкретную
разрешённую ExitPlan мутацию: protection change, reduce, close или иной
предусмотренный планом action.

# 3. StrategySignal

**StrategySignal** — факт того, что Entry Engine установил выполнение EntryPlan
конкретной Strategy на причинных данных.

**signal_id** — идентификатор одного StrategySignal одной exact Strategy.
Разные Strategy получают разные `signal_id`, даже если возникли на одном symbol
и в один момент.

`strategy_attempt` / attempt — exact lifecycle-попытка одной Strategy после
StrategySignal: она связывает проверку условий, capital reservation outcome,
EntryDecision и optional EntryExecutionRequest. Attempt не является fill и не
означает ACCEPTED.

**EntryDecision** — формализованный итог допуска одного strategy_attempt к
real Entry.

Каноническая таблица token -> entity:

| Token | Entity | Смысл |
| --- | --- | --- |
| ACCEPTED | EntryDecision | admission полностью успешен; разрешён EntryExecutionRequest |
| STRATEGY_CONDITION_REJECTED | EntryDecision | attempt отклонён Strategy condition |
| INSUFFICIENT_AVAILABLE_FUNDS | EntryDecision | capital reservation не получена |
| EXCHANGE_POSITION_OWNERSHIP_CONFLICT | EntryDecision | physical slot занят/pending/ownership не доказан |
| OPERATIONAL_SAFETY_BLOCKED | EntryDecision | required state известна, но несовместима с operational contract |
| STALE_OR_UNKNOWN_REQUIRED_STATE | EntryDecision | обязательная state missing/stale/unknown |
| EXPIRED | EntryDecision | attempt истёк до принятого request |
| CANCELLED | EntryDecision | attempt отменён до принятого request |
| REQUEST_PENDING | EntryExecutionRequest state | request создан, dispatch ещё не завершён |
| REQUEST_DISPATCHED | EntryExecutionRequest state | request передан execution path |
| REQUEST_ACKNOWLEDGED | EntryExecutionRequest state | request подтверждён downstream |
| REQUEST_EXPIRED | EntryExecutionRequest state | уже созданный request истёк |
| REQUEST_CANCELLED | EntryExecutionRequest state | уже созданный request отменён |
| REQUEST_RECONCILIATION_REQUIRED | EntryExecutionRequest state | outcome mutation нельзя доказать |
| REQUEST_TERMINAL | EntryExecutionRequest state | request завершён terminal outcome |
| EXCHANGE_POSITION_OWNERSHIP_INVARIANT_BROKEN | lifecycle fault | фактическая state нарушила правило одного physical owner |
| EXCHANGE_POSITION_MODE_MISMATCH | operational/lifecycle fault | mode/positionIdx несовместим с approved contract |

ACCEPTED возникает только после successful required-state validation,
physical slot claim и capital reservation.

EntryDecision.EXPIRED/CANCELLED и request-state
REQUEST_EXPIRED/REQUEST_CANCELLED — разные сущности.

# 4. Геометрия

**Геометрия** — рассчитанная по правилам Strategy пространственная структура
цены. Конкретная формула, таймфрейм, глубина и параметры принадлежат Strategy.

**GeometrySpec** — exact immutable спецификация расчёта геометрии из Strategy-owned параметров; все result-affecting поля входят в устойчивый fingerprint.

**Geometry state / snapshot** — причинно рассчитанное состояние exact GeometrySpec на момент T; наблюдаемый факт без торгового решения.

**Geometry timeline** — причинная временная последовательность versioned geometry states одного `symbol + GeometrySpec fingerprint`; может переиспользоваться Entry/Exit/Position Supervisor/Analyst без повторного вычисления той же геометрии.

**Таймфрейм** — длительность одной свечи/бара, например 5m или 15m.

**Глубина геометрии** — длительность причинной истории в минутах/часах, которую
использует конкретный расчёт. Глубина не равна автоматически числу баров.

Пример: глубина 9 часов означает 540 минут:
- на 5m это 108 свечей;
- на 15m это 36 свечей.

# 5. H9

**H9** — геометрия с временной глубиной ровно 9 часов = 540 минут.

H9 не означает 130 баров.

H9 не является глобальной константой проекта. Это название геометрии,
использующей 9-часовую глубину в той Strategy/исследовании, где она явно задана.

Для текущего первого Strategy Candidate H9 строится как совмещение:
- 5m-геометрии на глубине 9 часов;
- 15m-геометрии на глубине 9 часов.

Правила совмещения принадлежат Strategy.

Baseline текущего Candidate: `range_high=MAX(high)`, `range_low=MIN(low)` на глубине H9; Wilder ATR(200) отдельно на каждом timeframe; half-width зоны = `ATR*0.5`. ATR period и multiplier принадлежат Strategy и не являются global defaults.

# 6. H3

**H3** — геометрия с временной глубиной ровно 3 часа = 180 минут.

Для текущего исследовательского подхода H3 строится как совмещение:
- 5m-геометрии на глубине 3 часа;
- 15m-геометрии на глубине 3 часа.

Baseline H3 математически идентичен H9 по формуле компонентов/зон и правилам совмещения; отличается depth: 180m вместо 540m. Это 36 закрытых 5m и 12 закрытых 15m свечей для extrema. ATR period остаётся отдельным Strategy-owned параметром; baseline = 200.

H3 сейчас не является условием Entry текущего первого Strategy Candidate.
Она используется и исследуется как геометрия сопровождения/Exit.

Наблюдение H3 в момент Entry является исследовательским наблюдением и не
превращает H3 в условие входа.

# 7. Физические части геометрии

Чтобы не путать LONG и SHORT, основная терминология направленно-нейтральна.

**Нижняя граничная зона** — нижняя по абсолютной цене рассчитанная широкая
область геометрии.

**Верхняя граничная зона** — верхняя по абсолютной цене рассчитанная широкая
область геометрии.

**Рабочий диапазон** — пространство между внутренними границами нижней и
верхней граничных зон. Это не третья зона.

**Внутренняя граница** — край граничной зоны, обращённый к рабочему диапазону.

**Внешняя граница** — край граничной зоны, обращённый наружу от рабочего
диапазона.

LONG/SHORT не переименовывают физические объекты. Strategy отдельно определяет,
какую роль они играют для конкретного направления.

Термины `support`/`resistance` допускаются только как контекстная
интерпретация, но не как первичное имя физического объекта, если это создаёт
неоднозначность.

# 8. Текущая геометрия после Entry

**Entry snapshot** — исторический снимок фактов/геометрии, которыми был
обоснован состоявшийся Entry. Он сохраняется для аудита и не изменяется.

**Текущая геометрия** — геометрия, заново причинно рассчитанная на актуальных
данных по правилам Strategy после Entry.

Текущая H9 «дышит»: может двигаться вверх, вниз, стоять, менять границы и
структуру много раз, пока позиция живёт.

Для сопровождения важна текущая геометрия относительно:
- фиксированной Entry price;
- предыдущего состояния геометрии;
- текущей цены/позиции.

Историческая Entry-геометрия не должна искусственно подменять актуальную
геометрию сопровождения.

# 9. Стабилизация

**Стабилизация геометрии** — состояние, при котором геометрия не изменяется
достаточное заданное время и её микроколебания больше не рассматриваются как
рыночная вибрация для соответствующего правила Strategy.

Параметр стабилизации:
- задаётся в минутах;
- может отличаться для разных геометрий/компонентов;
- принадлежит Strategy;
- не является глобальной константой Entry Engine.

Найденное исследованием время стабилизации не становится каноном автоматически.

# 10. MAYAK и Dispatcher

**MAYAK** — независимый наблюдатель внешнего рынка.

**Dispatcher** — strategy-agnostic структурированный контекст рынка и торговой
ёмкости аккаунта.

**OBSERVED_CONTEXT** — контекст, объективно существовавший к моменту события.

**CONSUMED_CONTEXT** — контекст, который конкретный EntryPlan/ExitPlan реально
прочитал и использовал.

# 11. Execution

**ExecutionRequest** — immutable запрос на исполнение уже принятого Entry или
Exit decision с exact Strategy/Plan/decision lineage.

**EntryExecutionRequest** — ExecutionRequest для EntryDecision=ACCEPTED.

**ExitExecutionRequest** — ExecutionRequest для принятого ExitDecision и exact
StrategyPosition.

**Execution** — технический слой Exchange mutation и reconciliation.

**Exchange** — внешняя торговая площадка и источник фактической истины об
orders/fills/positions/account state.

**Exchange position slot** — физически отдельный position inventory,
определяемый exchange/account/product/instrument/position-mode identity.

exchange_position_key — стабильная technical identity physical slot.

**Physical slot claim** — durable exclusive claim одного real lifecycle на
exchange_position_key. Claim создаётся до EntryDecision=ACCEPTED, после
confirmed fill связывается с exact StrategyPosition и освобождается только
после доказанного отсутствия mutation либо final flat/reconciliation.

exchange_position_slot_claim_id — immutable identity physical slot claim.

**Position mode state** — причинный snapshot фактического Exchange position mode
для exact exchange/account/product/instrument scope с positionIdx, freshness и
provenance.

**One-way mode** — режим, где один symbol использует один directional slot.
Для текущего утверждённого Bybit Unified linear contract ожидается
positionIdx=0.

EXCHANGE_POSITION_OWNERSHIP_CONFLICT — штатный fail-closed EntryDecision:
требуемый physical slot уже имеет другого active/pending owner либо ownership
нельзя доказать. Новый real EntryExecutionRequest не создаётся. Сам outcome не
является аварией системы.

EXCHANGE_POSITION_OWNERSHIP_INVARIANT_BROKEN — lifecycle fault: durable,
runtime или Exchange state показывает более одного owner, потерянный ownership
binding либо иное нарушение single-owner invariant.

EXCHANGE_POSITION_MODE_MISMATCH — operational/lifecycle fault: фактический
position mode или positionIdx не соответствует owner-approved execution/slot
contract. Новые mutation блокируются до reconciliation либо отдельного
owner-approved изменения канона.

# 12. Аналитика и исследование

**Lifecycle Supervisor** — технический наблюдатель сквозного lifecycle от
Strategy activation/materialization до final close/economics. Проверяет exact
handoff/acknowledgement/IDs, но не создаёт торговых решений и не изменяет
позицию по собственной оценке.

**Position Supervisor** — наблюдение фактического состояния конкретной
StrategyPosition; не владелец Strategy/Exit.

**Strategy Monitor** — read-model/monitoring представление состояния exact
Strategy version × symbol × direction. Он не определяет universe и не создаёт
trading policy.

**MFE (Maximum Favorable Excursion)** — максимальное благоприятное для
направления Strategy отклонение цены/результата от Entry за выбранный lifecycle
интервал. Единицы и учёт комиссий должны указываться явно.

**MAE (Maximum Adverse Excursion)** — максимальное неблагоприятное для
направления Strategy отклонение от Entry за выбранный lifecycle интервал.
Единицы и учёт комиссий должны указываться явно.

**Analyst** — постфактум-аналитика и research без торговых прав.

**StrategyCoinFit** — strategy-specific историческая/исследовательская оценка
пригодности symbol для exact Strategy version. Принадлежит Analyst/research и
не является Dispatcher market rating.

**CoinMarketRating** — strategy-agnostic оценка объективного состояния symbol,
которую может публиковать Dispatcher только по отдельно утверждённой формуле.
Она не говорит, выгодна ли конкретная Strategy.

**Counterfactual trade / псевдосделка** — аналитическая моделируемая сделка,
которая могла бы быть открыта по Strategy, но не стала real Execution по явно
сохранённой причине, например INSUFFICIENT_AVAILABLE_FUNDS или
EXCHANGE_POSITION_OWNERSHIP_CONFLICT. Не резервирует капитал, не получает
physical slot claim и не имеет Exchange mutation rights.

**Исследование / research** — получение доказательств. Не канон и не Strategy.

**Evidence / доказательный материал** — результат исследования/runtime-наблюдения.

**Канон** — только явно утверждённые владельцем активные документы и точная
owner-approved Strategy version.

# 13. Капитал и lifecycle

**Atomic capital reservation** — техническая атомарная фиксация части
проверенного доступного капитала внутри real Entry admission до ACCEPTED и до
Exchange mutation. Она выполняется в одном all-or-nothing admission contract с
physical slot claim: отсутствие capital reservation не может оставить durable
slot claim. Не является отдельным торговым слоем и не ранжирует Strategy.

**First-come-first-served capital V1** — если несколько независимых Entry
конкурируют за ограниченный капитал, право получает первый успешно завершивший
atomic reservation. При нехватке средств real Execution не создаётся.

**POSITION_WITHOUT_EXIT_OWNER** — critical lifecycle fault: подтверждённая
StrategyPosition не имеет подтверждённого exact ExitPlan binding/Exit Engine
claim.

`CAPITAL_RESERVATION_STUCK` — lifecycle fault: reservation не может быть
безопасно финализирована/освобождена из-за неизвестного или несогласованного
order/fill state и требует reconciliation.

`POSITION_WITHOUT_CONFIRMED_INITIAL_PROTECTION` — critical lifecycle fault:
real StrategyPosition существует, но обязательная owner-approved initial
protection не подтверждена на Exchange.

**Initial protection / loss-containment contract** — owner-approved Strategy
policy, ограничивающая риск real position независимо от экспериментального
dynamic Exit. Exact stop/граница не является global default.

**Terminal loss-containment/close path** — заранее доказуемый путь, который
может привести real position к ограниченному риску/flat state без изобретения
новой trading policy. Для real Strategy хотя бы один такой путь обязателен.

**Emergency policy** — заранее утверждённая часть `lifecycle_policy`/
protection-failure contract, задающая, какое техническое действие разрешено при
конкретном operational fault. Наличие execution command `EMERGENCY_CLOSE` само
по себе policy не создаёт.

`owner kill` — явный owner control, запрещающий/останавливающий mutation в
заданном contract. Сам по себе не означает автоматический close уже открытой
позиции.

**Critical fault delivery** — durable механизм доставки critical operational/
lifecycle fault владельцу: alert/event с retry, acknowledgement либо explicit
escalation state. Наличие записи только в UI/read-model не считается доставкой.

`mainnet gate` — технический execution gate, разрешающий или запрещающий
реальную Exchange mutation. Не является Entry/Exit policy и не заменяет
StrategyActivation/execution permission.

`fail-closed` — обязательное поведение при missing/unknown/stale/unsupported
required state: не придумывать default и не продолжать опасную activation/
decision/dispatch/mutation; блокировать действие, сохранить evidence и требовать
reconciliation/исправление.

**reconciliation** — восстановление exact фактической истины между durable
lifecycle state и Exchange после unknown/ambiguous/stale результата. До
reconciliation нельзя освобождать ownership/capital или повторять mutation по
догадке.

**fee-aware break-even** — Strategy-owned protection rule, где уровень
безубытка учитывает явно заданные комиссии и, если утверждено, slippage/другие
издержки. Не существует как глобальное число по умолчанию.

**hedge** — Strategy-owned lifecycle policy противоположной экспозиции.
Сам термин не означает Exchange hedge-mode. Реальное исполнение требует exact
request/reservation/slot lineage, capital accounting и совместимый Exchange
position-mode contract. В текущем one-way contract same-symbol hedge unsupported
и обязан fail-closed; встречный order не может молча считаться hedge.

# 14. Качество данных

`NO_DATA`, `UNKNOWN`, `STALE`, `PARTIAL` — реальные состояния качества.
Их нельзя превращать в ноль, `NONE`, neutral или safe.

# 15. Runtime / release / validation terms

SHADOW — режим decision/lifecycle логики без права создавать real Exchange
mutation.

LIVE EQUIVALENCE — доказанное соответствие production live path проверенному
SHADOW/replay/test path по semantics, inputs, policy lineage, consumer behavior
и execution contract. Не равно «тесты прошли».

MICRO_LIVE — owner-approved ограниченный real execution этап после LIVE
EQUIVALENCE с отдельными limits/gates/evidence.

LIVE — owner-approved режим реальной Exchange mutation после выполнения
обязательного LIVE-arm readiness contract. DEPLOY, service liveness или
mainnet-capable code сами по себе не означают LIVE.

**Runtime liveness verified** — доказано, что expected service/process запущен,
не падает и публикует ожидаемый heartbeat/read-model. Это не доказывает
правильность behavior на требуемом lifecycle scenario.

**Runtime behavior verified** — на фактическом runtime path доказано заявленное
поведение конкретного contract/scenario с causal evidence. Нулевая выборка,
отсутствие позиции или просто faults=0 не является behavior verification.

**RUNTIME VERIFIED** — umbrella-статус, который нельзя писать как одиночное YES
без указания dimensions. В status matrix отдельно указываются LIVENESS и
BEHAVIOR.

runtime_build_ref — audit identity реально загруженного runtime artifact/source
commit/build.

**Implementation pass Pn** — временная нумерация проходов разработки/
стабилизации; не слой архитектуры и не торговый термин.

# 16. Запрещённые/исторические обозначения

**M3** — ошибочный исторический артефакт голосового распознавания слова
`Entry`. Самостоятельного смысла, таймфрейма, Strategy или математической
семантики не имеет. В новом коде, документации и исследовании не использовать.

**H4** — не использовать как замену H3.

**«верх H3», «низ H9», «линия H3/H9», «h3_edge»** без уточнения физического
объекта считаются неоднозначными. Нужно указывать конкретную граничную зону,
внутреннюю/внешнюю границу, рабочий диапазон или точную метрику.
