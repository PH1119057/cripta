# Текущее устройство проекта Cripta

Версия документа: 1.0  
Актуально для кода начиная с commit `aad1aab6ab1f673710656ef741ebae444a69a5f1`.  
Дата состояния: 2026-08-31.

Этот документ — короткая карта действующей системы. Он помогает быстро найти
исходный код, тесты, эксплуатационные компоненты, данные и архитектурные
контракты. Подробные правила имеют приоритет согласно
`docs/DOCUMENT_AUTHORITY_RU.md` и `docs/PROJECT_ARCHITECTURE_RU.md`.

## 1. Назначение системы

Cripta — серверная система наблюдения за рынком, причинной оценки условий,
формирования M3-сигналов, исполнения сделок на Bybit KZ, сопровождения позиций и
последующего анализа результатов.

Текущая целевая цепочка:

```text
Рынок
→ Маяк
→ dispatcher_handoff
→ Диспетчер ENTRY/HOLD
→ стратегия M3
→ Entry
→ Execution
→ Bybit KZ
→ Диспетчер HOLD + Position Supervisor
→ Exit/Risk
→ Analyst
```

Маяк не принимает торговых решений и не знает о наших позициях, PnL или
сигналах Entry. Диспетчер не отправляет ордера. Торговое решение принимает
контроллер стратегии, а реальные команды исполняет private runtime.

## 2. Основные каталоги GitHub

| Путь | Что находится |
|---|---|
| `src/bybit_workbench/` | Основная библиотека: Entry, Supervisor, Bybit, исполнение, исследования, persistence и UI-модели |
| `production/src/bybit_workbench/strategy_dispatcher/` | Каноническая реализация Диспетчера стратегий |
| `operations/monitoring/` | Серверные процессы Маяка, Entry scanner, Supervisor и Analyst-коррелятора |
| `operations/connectivity/` | Private Bybit runtime, reconciliation, постановка и исполнение команд |
| `operations/dashboard/` | Портал, API, Archive V2 и пользовательские торговые настройки |
| `operations/devtools/` | `cripta-state`, `cripta-diff-report`, `cripta-gate`, patch/rollback/soak |
| `operations/systemd/` | Канонические unit-файлы серверных служб |
| `config/strategy_dispatcher/` | Исполняемые профили Диспетчера, JSON Schema и исследовательские кандидаты |
| `tests/` | Полный набор модульных, контрактных, архитектурных и runtime-тестов |
| `research/`, `research_tools/`, `scripts/` | Универсальные исследования и эксплуатационные сценарии |
| `docs/` | Активные архитектурные документы, контракты и исследовательские протоколы |

Старые каталоги вида `P44_*`, `P47_*`, `ENTRY_BOT_*`, `EO*`, `SE*` — это
исторические пакеты проходов и исправлений. Они полезны для происхождения
решений, но не являются автоматически действующим production runtime.

## 3. Действующие серверные компоненты

| Компонент | Роль | Основной код |
|---|---|---|
| Маяк V2 | Независимо измеряет рынок, денежный поток, OI, ликвидации, стакан и качество источников | `operations/monitoring/mayak_v2.py`, `src/bybit_workbench/mayak/` |
| Strategy Dispatcher | Сопоставляет `dispatcher_handoff` с профилями ENTRY/HOLD | `production/src/bybit_workbench/strategy_dispatcher/` |
| Entry scanner | Формирует причинные M3/Entry-сигналы и ведёт исследовательские пути | `operations/monitoring/entry_shadow_scanner.py`, `src/bybit_workbench/entry_bot/` |
| M3 Entry consumer | Потребляет только существовавшую до сигнала оценку Диспетчера (`CONSUMED_CONTEXT`) | `operations/connectivity/private_runtime.py` |
| Private runtime | Сверяет Bybit, исполняет команды, сохраняет fills/orders/positions и ставит исходную защиту | `operations/connectivity/private_runtime.py` |
| Position Supervisor | После fill независимо оценивает состояние каждой позиции | `operations/monitoring/position_supervisor.py`, `src/bybit_workbench/position_supervisor/` |
| HOLD consumer | Совмещает оценку HOLD Диспетчера с состоянием Supervisor | `operations/monitoring/position_supervisor.py` |
| Analyst | Связывает снимки, решения, команды, исполнения и сопровождение без изменения live-настроек | `operations/monitoring/causal_context_correlator.py` |
| Dashboard | Показывает торговлю, наблюдение, настройки, отчёты и состояние служб | `operations/dashboard/` |
| Archive V2 | Формирует полный диагностический снимок кода, базы, статистики, отчётов и журналов | `operations/dashboard/archive_v2.py` |

## 4. Текущая торговая политика

Исполняемые профили M3:

- `M3_V1_LONG_ENTRY`;
- `M3_V1_SHORT_ENTRY`;
- `M3_V1_LONG_HOLD`;
- `M3_V1_SHORT_HOLD`.

Версия профилей: `1.0.0-owner-live`.  
Имя политики: `m3_full_live_v1`.

Новый вход разрешается только по причинной оценке Диспетчера, которая уже
существовала к моменту сигнала. Будущая или ретроспективно подставленная оценка
запрещена. Неполные, устаревшие или прогревающиеся обязательные данные закрывают
новый вход.

После подтверждённого fill Entry перестаёт владеть позицией. Сопровождение
выполняют Dispatcher HOLD и Position Supervisor. Совместный досрочный выход
разрешён только по контрактным сочетаниям их состояний. Серверный структурный
стоп −1% остаётся последней защитой.

## 5. PostgreSQL

Основные схемы:

| Схема | Назначение |
|---|---|
| `mayak_v2` | Снимки, события, минутные данные, журнал наблюдений и ликвидации |
| `strategy_dispatcher` | Запуски Диспетчера и оценки профилей |
| `monitoring` | Сигналы и независимое наблюдение |
| `runtime` | Настройки, execution gate, решения Entry, команды, fills, позиции, ордера, wallet и reconciliation |
| `supervisor` | Снимки/переходы Position Supervisor и потреблённый HOLD-контекст |
| `research_context` | Причинные связи Analyst между событиями системы |
| `control`, `safety` | Управление разрешениями и эксплуатационная безопасность |

Полный PostgreSQL dump содержит приватную торговую историю и эксплуатационные
идентификаторы. Его нельзя публиковать в открытом GitHub. Для быстрого анализа
через GitHub следует хранить:

1. описание схемы и миграции;
2. список таблиц и полей;
3. обезличенную компактную аналитическую выгрузку без account/order/exec ID;
4. небольшой синтетический или очищенный fixture для тестов.

Полный dump, фактические журналы и торговые отчёты остаются в Archive V2 и в
папке `Reports`, доступной владельцу.

## 6. Что хранится в GitHub

GitHub должен содержать:

- исходный код и конфигурацию;
- все тесты;
- активную документацию;
- systemd units и эксплуатационные инструменты;
- SQL-схемы/миграции;
- безопасные обезличенные fixtures;
- сведения о версии и структуре проекта.

GitHub не должен содержать:

- ручные архивы и отчёты владельца из `Reports`;
- полный production PostgreSQL dump;
- реальные журналы служб;
- API-ключи, токены, пароли и credentials;
- полные рыночные datasets и временные runtime-файлы.

## 7. Где находится полная фактическая информация

- Канонический серверный код: `/srv/cripta/source_checkout`.
- Production runtime: `/srv/cripta/production`, `/srv/cripta/monitoring`,
  `/srv/cripta/connectivity`, `/srv/cripta/dashboard`.
- Большие datasets: `/data/cripta`.
- Полные диагностические архивы: диск `K:`, папка `Reports`.
- GitHub: быстрый доступ к коду, документации, тестам и безопасной структуре БД.

## 8. Контроль изменений этого документа

Этот файл используется как источник проекта в ChatGPT. После изменения
архитектуры, путей, активной политики, состава PostgreSQL или границ владения
компонентов необходимо:

1. обновить версию и commit в начале документа;
2. commit/push новой версии в GitHub;
3. явно сообщить владельцу: **«Карта проекта изменилась — обновите источник
   `CURRENT_PROJECT_MAP_RU.md` в проекте ChatGPT»**;
4. кратко перечислить изменившиеся разделы.

Без такого сообщения владелец может считать сохранённую в ChatGPT копию
актуальной, хотя архитектура уже изменилась.
