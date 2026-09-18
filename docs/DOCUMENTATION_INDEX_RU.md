# CRIPTA — активный комплект документации

**Версия:** 2.3  
**Дата:** 2026-09-18  
**Статус:** канонический индекс документации

# 1. Цель

В проекте существует один небольшой активный комплект документов.
Физическое наличие старого файла в repository/history не даёт ему authority.

# 2. Роли и приоритет

```text
META — docs/CHATGPT_INTERACTION_RULES_RU*.md
       регулирует чтение, проверку и взаимодействие ChatGPT;
       не задаёт торговую архитектуру.

LEVEL 0 — подтверждённое текущее решение владельца.
          Если оно меняет канон, сначала обязательна новая версия канона.

LEVEL 1 — CRIPTA_ASSISTANT_WORK_RULES_RU_*.md
          CRIPTA_ARCHITECTURE_RULES_RU_*.md
          docs/CRIPTA_GLOSSARY_RU*.md

LEVEL 2 — docs/TRADING_CONTOUR_RU*.md
          docs/OBSERVATION_ANALYTICS_RU*.md

LEVEL 3 — docs/CURRENT_PROJECT_MAP_RU*.md

LEVEL H — Git history, archive, patch payload docs,
          старые research/evidence/runbook/handoff/Pxx/EO/SE/PASS.
```

Если новое решение владельца конфликтует с каноном:

```text
CANON_CONFLICT=YES
HARD_STOP=YES
CANON_UPDATE_REQUIRED=YES
OWNER_DECISION_REQUIRED=YES
```

После подтверждения владельца сначала обновляется канон, затем код.

# 3. Восемь файлов ChatGPT Project Source

1. `docs/CHATGPT_INTERACTION_RULES_RU*.md`
2. `CRIPTA_ASSISTANT_WORK_RULES_RU_*.md`
3. `CRIPTA_ARCHITECTURE_RULES_RU_*.md`
4. `docs/DOCUMENTATION_INDEX_RU*.md`
5. `docs/CRIPTA_GLOSSARY_RU*.md`
6. `docs/CURRENT_PROJECT_MAP_RU*.md`
7. `docs/TRADING_CONTOUR_RU*.md`
8. `docs/OBSERVATION_ANALYTICS_RU*.md`

Используется семейство имени, а не номер версии/UI suffix.
`CHATGPT_INTERACTION_RULES_RU*.md` читается первым.

# 4. AGENTS.md

`AGENTS*.md` — GitHub-only bootstrap для Codex/разработчика.
Он не создаёт самостоятельный архитектурный контракт.

# 5. Обязательный pre-read нового чата

Сначала:
1. `CHATGPT_INTERACTION_RULES_RU*.md`.

Затем:
2. `CRIPTA_ASSISTANT_WORK_RULES_RU_*.md`;
3. `CRIPTA_ARCHITECTURE_RULES_RU_*.md`;
4. `DOCUMENTATION_INDEX_RU*.md`;
5. `CRIPTA_GLOSSARY_RU*.md`;
6. `CURRENT_PROJECT_MAP_RU*.md`.

Перед Strategy / Entry / Exit / Execution:
- `TRADING_CONTOUR_RU*.md`.

Перед MAYAK / Dispatcher / monitoring / Lifecycle Supervisor /
Position Supervisor / Analyst / research / replay / OOS / holdout:
- `OBSERVATION_ANALYTICS_RU*.md`.

Если затрагиваются оба контура — читать оба.

# 6. Историческая изоляция

В активном каталоге `docs/` находятся только текущие канонические документы.

Исторические материалы могут физически сохраняться в `archive/**`, Git
history и внутри старых неизменяемых patch/research artifacts.

По умолчанию исполнитель не читает и не использует:
- `archive/**`;
- `patch_backups/**`;
- `*/payload/docs/**`;
- старые Pxx / EO / SE / PASS / handoff / runbook.

Открывать их можно только по явной исторической задаче.

# 7. Исследования и evidence

Исследование любой давности остаётся evidence.

```text
RESEARCH RESULT
-> OWNER DECISION
-> CANON / STRATEGY UPDATE
-> TEST / SHADOW
-> LIVE EQUIVALENCE
-> MICRO_LIVE
-> LIVE
```

# 8. Project Instructions

Project Instructions должны быть тонким bootstrap и ссылаться на семейства
имён с `*`, а не дублировать полный содержательный канон.

Они обязаны найти source of truth, загрузить правильный pre-read, применить
Hard Stop, не использовать память/history как канон и различать статусы.

# 9. Обновление Project Source

1. обновляется GitHub `main`;
2. синхронизируется `/srv/cripta/source_checkout`;
3. формируется набор восьми текущих файлов;
4. владелец полностью заменяет старые Project Source;
5. дополнительные материалы не получают authority автоматически.
