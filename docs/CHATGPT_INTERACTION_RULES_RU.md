# CRIPTA — правила взаимодействия ChatGPT с владельцем

**Версия:** 1.9
**Дата:** 2026-10-02
**Статус:** обязательный канонический META-контракт взаимодействия

Этот документ регулирует способ совместной работы владельца проекта и ChatGPT.
Он читается ПЕРВЫМ, но не определяет торговую архитектуру и не переопределяет
смысл Strategy / Entry / Exit / Execution / MAYAK / Dispatcher.

# 1. Идентичность документов

Документ определяется по устойчивому смысловому имени / семейству, а не по
номеру версии или техническому суффиксу UI.

Использовать семейства:

- `CHATGPT_INTERACTION_RULES_RU*.md`
- `CRIPTA_ASSISTANT_WORK_RULES_RU_*.md`
- `CRIPTA_ARCHITECTURE_RULES_RU_*.md`
- `DOCUMENTATION_INDEX_RU*.md`
- `CRIPTA_GLOSSARY_RU*.md`
- `CURRENT_PROJECT_MAP_RU*.md`
- `TRADING_CONTOUR_RU*.md`
- `OBSERVATION_ANALYTICS_RU*.md`
- `DEVELOPMENT_RELEASE_RULES_RU*.md` — routed process canon;
- `RESEARCH_COMPUTE_RULES_RU*.md` — routed process canon;
- `SECURITY*.md` — обязательный technical security baseline;
- `AGENTS*.md` — резервный bootstrap.

Суффиксы `(2)`, `(7)`, `(8)`, `V1`, `V2` и подобные части отображаемого
имени не являются идентичностью документа.

Физически вся текущая содержательная документация CRIPTA хранится в `docs/`.
Корневые `README.md` и `AGENTS.md` — только технические entrypoints/bootstrap и
не создают отдельный уровень authority. Семейство документа остаётся
path-independent. Если меняются active document set, пути, mandatory pre-read
или routed reading, `docs/DOCUMENTATION_INDEX_RU.md`, корневые `README.md` и
`AGENTS.md` обновляются одним documentation changeset.

Если доступны несколько копий одного семейства, порядок актуальности:

1. GitHub `PH1119057/cripta:main`;
2. синхронизированный `/srv/cripta/source_checkout`;
3. при недоступности source of truth — поля `Версия / Дата / Статус` внутри
   документа и `DOCUMENTATION_INDEX_RU*.md`.

Если актуальность нельзя определить однозначно:

```text
HARD_STOP=YES
OWNER_DECISION_REQUIRED=YES
```

Не угадывать.

# 2. Source of truth и память ChatGPT

Авторитетный source of truth:
- GitHub `PH1119057/cripta:main`.

`/srv/cripta/source_checkout` — синхронизированное operational mirror GitHub
`main`. В нормальном checkpoint его HEAD обязан совпадать с GitHub `main`,
но он не становится вторым независимым источником истины.

Project Source, File Library, память модели, старые чаты, старые ZIP, локальный
`C:\cripta`, Git history и исторические документы — только вспомогательный
контекст.

При любом расхождении authority имеет GitHub `main`; source checkout сначала
синхронизируется и только затем используется для deployment/runtime forensic.

# 3. META и приоритет канона

Этот файл имеет роль `META`: он определяет, как ChatGPT должен найти,
прочитать, проверить и применить канон.

`META` не является уровнем торговой архитектуры и не может сам вводить
торговый параметр. Приоритет содержательных документов задаётся только
`DOCUMENTATION_INDEX_RU*.md`.

# 4. Решение владельца и конфликт с каноном

Явное текущее решение владельца имеет `LEVEL 0` как источник намерения.

Если оно совместимо с активным каноном, работа продолжается в его рамках.

Если новое высказывание владельца меняет или противоречит активному канону,
нельзя молча считать, что архитектура уже изменена:

```text
CANON_CONFLICT=YES
HARD_STOP=YES
CANON_UPDATE_REQUIRED=YES
OWNER_DECISION_REQUIRED=YES
```

Нужно кратко указать конфликт и какой документ/контракт надо изменить.

После подтверждения владельца:

```text
OWNER DECISION
-> CANON UPDATE
-> IMPLEMENTATION
-> TEST
-> GITHUB
-> DEPLOY
-> RUNTIME EVIDENCE
```

Если владелец поясняет, что имел в виду другое, канон не меняется.

# 5. Не додумывать терминологию

Канонический словарь — `CRIPTA_GLOSSARY_RU*.md`.

Если термин отсутствует в словаре или допускает несколько физических смыслов:

```text
TERM_AMBIGUOUS=YES
HARD_STOP=YES
OWNER_DECISION_REQUIRED=YES
```

Не выбирать трактовку самостоятельно и не считать распознавание речи
доказательством термина.

# 6. Не тащить прошлое в новую задачу

Исследование любой давности не является каноном.

Старые методики, Entry V1, прежние thresholds, горизонты, профили и результаты
использовать только по явной задаче сравнения/воспроизводимости либо при
доказанной совместимости dataset.

Переиспользование данных не означает переиспользование старой логики.

# 7. Не смешивать факт, вывод и статус

```text
CHECKED HERE
NOT CHECKED HERE
FINDING
RESEARCH RESULT
OWNER DECISION
CANON
IMPLEMENTED
DEPLOYED
RUNTIME VERIFIED
```

Commit != deploy. Deploy != runtime verification.

# 8. Сначала проверка, потом утверждение

При вопросах о текущем коде, GitHub, сервере, runtime, БД, service state,
mainnet gate, Strategy activation или фактических данных сначала использовать
актуальный источник.

Память модели или старый чат не являются текущей проверкой.

# 9. Не повторять уже закрытые вопросы

Не задавать владельцу повторно вопрос, на который он уже дал однозначный ответ.

Уточнение допустимо только при реальной неоднозначности, влияющей на
расчёт/код/архитектуру, Hard Stop или отсутствии обязательной информации.

# 10. Язык общения

Общение с владельцем — преимущественно на русском языке.

Английский сохранять для точных code identifiers, API, DB tokens, имён
классов/полей и случаев, где перевод ухудшает однозначность.

В торговых отчётах использовать выражение «после комиссий».

# 11. Формат ответа

По умолчанию сначала дать прямой ответ, затем только необходимое объяснение.
Не скрывать существенные ограничения и непроверенные места.

# 12. Project Instructions — тонкий bootstrap

Project Instructions должны быть устойчивым загрузчиком канона, а не второй
копией архитектурных документов.

OWNER CHECKED HERE 2026-09-28: текущий ChatGPT UI принимает не более 8000
символов в Project Instructions. Это operational product constraint, а не
архитектурный лимит. Project Instructions обязаны оставаться <= 8000 символов;
если bootstrap разрастается, детали переносятся в canonical docs, а не
дублируются в Instructions.

В них должны оставаться только:
- source of truth;
- семейства канонических файлов;
- обязательный pre-read нового чата;
- routing специализированного pre-read;
- Hard Stop / терминологический Hard Stop;
- правило «память и старые чаты не канон»;
- различение статусов;
- язык взаимодействия.

Полное описание MAYAK / Dispatcher / Strategy / Entry / Exit / Execution,
конкретные торговые параметры, research conclusions и runtime snapshot должны
читаться из активных документов.

Все 11 current docs могут и должны быть доступны в ChatGPT Project Source.
OWNER CHECKED HERE 2026-09-28: текущий UI допускает максимум 12 Project Source
files. Поэтому current bundle = 11, capacity = 1. Нельзя дробить current
canonical document только ради удобства чтения, если это увеличивает bundle.
Новый 12-й current file требует owner decision; 13-й запрещён до предварительной
консолидации existing docs.

Само присутствие файла в Project Source не делает его mandatory every-chat
pre-read. SECURITY читается в base pre-read для любой содержательной работы.
Тяжёлые process contracts читаются по task route: DEVELOPMENT_RELEASE перед
patch/Git/PostgreSQL/release/deploy; RESEARCH_COMPUTE перед research/replay/OOS/
holdout/large-data/long-compute.

Небольшое дублирование фундаментальных safety-инвариантов допустимо, если оно
осознанно. При изменении такого инварианта Project Instructions и канон должны
обновляться в одной документационной ревизии.

Любая documentation revision дополнительно соблюдает постоянный maintenance
contract из `docs/DOCUMENTATION_INDEX_RU*.md §17`: topology/bootstrap,
renumber/reference audit, dated MAP checkpoints, release/runtime identity и
запрет молча закрывать open architecture decisions.

Exact UI Project Instructions text ведётся как derived artifact
`operations/bootstrap/CHATGPT_PROJECT_INSTRUCTIONS_RU.txt` по INDEX §17.10.
Он не является новым каноническим документом и не входит в Project Source.

# 13. Исторические файлы и поиск

Активным каноном являются только документы из `DOCUMENTATION_INDEX_RU*.md`.

По умолчанию не использовать как текущую инструкцию и не включать в широкий
поиск архитектуры:
- `archive/**`;
- `patch_backups/**`;
- исторические `*/payload/docs/**`;
- старые Pxx / EO / SE / PASS / handoff / runbook;
- Git history.

Открывать их только по явной исторической задаче.

Если current source содержит legacy identifier, его можно исследовать как
`FINDING`, но соседний исторический документ не становится каноном.

# 14. Перед каждым содержательным ответом

1. учтён ли этот META-контракт;
2. понятны ли термины владельца;
3. не конфликтует ли ответ с current owner decision;
4. не используется ли history/research как скрытый канон;
5. не выдаётся ли непроверенное за проверенное;
6. нужен ли специализированный pre-read;
7. не требуется ли сначала обновить канон;
8. если меняется документация — выполнен ли INDEX §17 documentation gate;
9. если меняются Project Instructions/Project Source — соблюдены ли UI limits
   8000 chars / 12 files.
# 15. Architecture capability Hard Stop

Явное решение владельца может изменить архитектуру, но исполнитель не имеет
права молча уничтожить уже утверждённую системную capability только потому,
что её прежнему owner запрещено ею владеть.

Перед архитектурно чувствительной mutation обязательно проверить:

~~~text
CAPABILITY
OLD_OWNER
OLD_CONTRACT
CHANGE
NEW_OWNER
NEW_CANON
NEW_IMPLEMENTATION
MIGRATION
TEST / EVIDENCE
~~~

Если изменение удаляет, ослабляет, переносит или делает passive-only ранее
утверждённую capability, а новый owner/contract/implementation ещё не
определены и не готовы, применяется:

~~~text
ARCHITECTURE_CAPABILITY_GAP=YES
HARD_STOP=YES
CANON_UPDATE_REQUIRED=YES
OWNER_DECISION_REQUIRED=YES
MIGRATION_COMPLETE=NO
OLD_CAPABILITY_DECOMMISSION_ALLOWED=NO
~~~

Исполнитель обязан прямо сообщить владельцу, какая capability исчезнет или
останется без consumer/owner, а не считать локально правильное разделение
ответственности завершённой архитектурой.

После owner decision порядок остаётся:

~~~text
CANON UPDATE
-> IMPLEMENTATION
-> TEST
-> GITHUB
-> DEPLOY [если нужен]
-> RUNTIME EVIDENCE
~~~
