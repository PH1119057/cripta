# CRIPTA — канонический словарь

**Версия:** 1.4
**Дата:** 2026-09-19
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

**EntryDecision** — формализованный итог attempt. Минимальные состояния:
`ACCEPTED`, `STRATEGY_CONDITION_REJECTED`,
`INSUFFICIENT_AVAILABLE_FUNDS`, `OPERATIONAL_SAFETY_BLOCKED`,
`STALE_OR_UNKNOWN_REQUIRED_STATE`, `EXPIRED`, `CANCELLED`.
`ACCEPTED` возникает только после успешной обязательной reservation.

# 4. Геометрия

**Геометрия** — рассчитанная по правилам Strategy пространственная структура
цены. Конкретная формула, таймфрейм, глубина и параметры принадлежат Strategy.

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

# 6. H3

**H3** — геометрия с временной глубиной ровно 3 часа = 180 минут.

Для текущего исследовательского подхода H3 строится как совмещение:
- 5m-геометрии на глубине 3 часа;
- 15m-геометрии на глубине 3 часа.

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

**ExecutionRequest** — неизменяемый запрос на исполнение уже принятого Entry
или Exit decision с точной Strategy/Plan/decision lineage.

**EntryExecutionRequest** — ExecutionRequest для принятого EntryDecision.

**ExitExecutionRequest** — ExecutionRequest для принятого ExitDecision и exact
StrategyPosition.

**Execution** — технический слой биржевой мутации и reconciliation.

**Exchange** — внешняя торговая площадка и источник фактической истины о
orders/fills/positions/account state.

**Exchange position slot** — физически отдельный position inventory,
определяемый exchange/account/instrument/position-mode identity. Логические
StrategyPosition не могут считаться физически независимыми, если они попадают в
один slot.

`exchange_position_key` — стабильная техническая identity такого slot в нашем
lifecycle. Для текущего Bybit Unified linear one-way она включает account,
linear/settle context, symbol и `positionIdx=0`.

**One-way mode** — режим биржи, где один symbol использует один directional
slot (`positionIdx=0`). Противоположный order может уменьшить/закрыть
существующую позицию, поэтому независимые Strategy не получают право
одновременно владеть этим slot.

`EXCHANGE_POSITION_OWNERSHIP_CONFLICT` — fail-closed outcome/fault:
физический slot занят, имеет pending mutation либо его ownership нельзя
однозначно доказать. Новый независимый Entry не отправляется на Exchange.

# 12. Аналитика и исследование

**Lifecycle Supervisor** — технический наблюдатель сквозного lifecycle от
Strategy activation/materialization до final close/economics. Проверяет exact
handoff/acknowledgement/IDs, но не создаёт торговых решений и не изменяет
позицию по собственной оценке.

**Position Supervisor** — наблюдение фактического состояния конкретной
StrategyPosition; не владелец Strategy/Exit.

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
которая могла бы быть открыта по Strategy, но не стала real Execution
(например, из-за недостатка доступного капитала). Не резервирует средства и не
имеет exchange mutation rights.

**Исследование / research** — получение доказательств. Не канон и не Strategy.

**Evidence / доказательный материал** — результат исследования/runtime-наблюдения.

**Канон** — только явно утверждённые владельцем активные документы и точная
owner-approved Strategy version.

# 13. Капитал и lifecycle

**Atomic capital reservation** — техническая атомарная фиксация части
проверенного доступного капитала ВНУТРИ формирования real EntryDecision до
`ACCEPTED` и до биржевой отправки. Успех reservation позволяет создать
`ACCEPTED`; нехватка средств создаёт `INSUFFICIENT_AVAILABLE_FUNDS`.
Не является отдельным торговым слоем и не ранжирует Strategy.

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
Сам термин не означает Bybit hedge-mode. Реальное исполнение требует exact
request/reservation/lineage, capital accounting и совместимый Exchange position
mode contract.

# 14. Качество данных

`NO_DATA`, `UNKNOWN`, `STALE`, `PARTIAL` — реальные состояния качества.
Их нельзя превращать в ноль, `NONE`, neutral или safe.

# 15. Runtime / release / validation terms

`SHADOW` — режим, в котором decision/lifecycle логика работает на реальных
или replay facts без права создавать реальную Exchange mutation.

`LIVE EQUIVALENCE` — доказанное соответствие production live path
проверенному SHADOW/replay/test path по semantics, inputs, policy lineage,
consumer behavior и execution contract в пределах заявленного scope. Не равно
«тесты прошли».

`MICRO_LIVE` — owner-approved ограниченный real execution этап после
LIVE EQUIVALENCE, с отдельными gates/limits/evidence. Не является автоматическим
следствием DEPLOY.

`runtime_build_ref` — audit identity реально загруженного runtime artifact/
source commit/build. Не является торговым параметром Strategy, но нужен для
воспроизводимости, LIVE EQUIVALENCE и доказательства того, какой код исполнялся.

**Implementation pass Pn** (P3/P9/P10 и т.п.) — временная нумерация проходов
разработки/стабилизации. Это не слой архитектуры и не торговый термин; детали
pass читаются только в текущем implementation plan при соответствующей задаче.

# 16. Запрещённые/исторические обозначения

**M3** — ошибочный исторический артефакт голосового распознавания слова
`Entry`. Самостоятельного смысла, таймфрейма, Strategy или математической
семантики не имеет. В новом коде, документации и исследовании не использовать.

**H4** — не использовать как замену H3.

**«верх H3», «низ H9», «линия H3/H9», «h3_edge»** без уточнения физического
объекта считаются неоднозначными. Нужно указывать конкретную граничную зону,
внутреннюю/внешнюю границу, рабочий диапазон или точную метрику.
