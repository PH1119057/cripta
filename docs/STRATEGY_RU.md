# STRATEGY — активный контракт торговой политики

**Версия:** 1.0  
**Дата:** 2026-09-18  
**Статус:** активный канонический документ слоя STRATEGY

# 1. Смысл

Strategy — пассивный справочник точных правил одного способа торговли.

Она не мониторит рынок и сама не создаёт сигнал во времени.
Активное наблюдение выполняет Entry Engine/Exit runtime, используя планы,
материализованные из Strategy.

# 2. StrategyCard

`StrategyCard`:
- утверждается владельцем;
- immutable после утверждения;
- имеет version/fingerprint;
- не меняется автоматически от статистики;
- содержит все decision/execution-affecting параметры конкретной Strategy.

Изменение любого торгового параметра = новая version.

# 3. Что принадлежит Strategy

Минимально Strategy может определять:
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
- hedge, если включён.

Если параметр отсутствует, Entry/Exit/Execution не имеют права подставить
исторический торговый default.

# 4. H9/H3 как параметры Strategy

H9 и H3 — названия геометрий по временной глубине, а не системные константы.

Текущая исследуемая первая Strategy использует H9:
- 5m на 9 часах;
- 15m на 9 часах;
- их совмещение по правилам Strategy.

Это не означает, что любая будущая Strategy обязана иметь H9.

H3:
- 5m на 3 часах;
- 15m на 3 часах;
- их совмещение.

H3 сейчас не является Entry condition текущей первой Strategy.

# 5. Стабилизация

Время стабилизации задаётся в Strategy в минутах отдельно там, где оно нужно.
Никакое найденное исследованием значение не становится default универсального
Entry Engine.

# 6. Несколько Strategy

Одновременно могут быть активны несколько Strategy, включая противоположные:
- Strategy A может дать LONG;
- Strategy B в тот же момент может дать SHORT.

Они независимы. Entry Engine не выбирает между ними.

# 7. Материализация

Из exact Strategy version создаются immutable `EntryPlan` и `ExitPlan`.

Любое decision/execution-affecting поле должно иметь доказанный end-to-end
consumer. Неподдержанный параметр = fail-closed для активации, а не silent
ignore.

# 8. Исследование

Исследование никогда не меняет Strategy автоматически.

Результат становится торговой policy только после отдельного решения владельца
и создания новой Strategy version.
