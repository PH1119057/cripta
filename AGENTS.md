# CRIPTA — bootstrap для исполнителя

Вся текущая содержательная документация CRIPTA хранится в `docs/`.
Этот `AGENTS.md` — только корневой bootstrap и не является отдельным каноном.

## Mandatory pre-read

Для ChatGPT первым читать:

1. `docs/CHATGPT_INTERACTION_RULES_RU*.md`
2. `docs/CRIPTA_ASSISTANT_WORK_RULES_RU_*.md`
3. `docs/CRIPTA_ARCHITECTURE_RULES_RU_*.md`
4. `docs/DOCUMENTATION_INDEX_RU*.md`
5. `docs/CRIPTA_GLOSSARY_RU*.md`
6. `docs/CURRENT_PROJECT_MAP_RU*.md`

При работе с конкретным слоем дополнительно читать его active routed document
из `docs/DOCUMENTATION_INDEX_RU*.md`.

Затем по task route:

- Strategy / Entry / Exit / Execution -> `docs/TRADING_CONTOUR_RU*.md`
- MAYAK / Dispatcher / Monitoring / Lifecycle Supervisor / Position Supervisor / Analyst -> `docs/OBSERVATION_ANALYTICS_RU*.md`
- patch / Git / PostgreSQL / package / release / deploy / rollback -> `docs/DEVELOPMENT_RELEASE_RULES_RU*.md`
- research / replay / OOS / holdout / large-data / long-compute -> `docs/RESEARCH_COMPUTE_RULES_RU*.md`

Technical security baseline для всей работы: `docs/SECURITY.md`.

## Source of truth

```text
AUTHORITATIVE: GitHub PH1119057/cripta:main
OPERATIONAL MIRROR: /srv/cripta/source_checkout
```

Project Source, память, старые чаты/ZIP, локальные копии, Git history,
`archive/**`, `patch_backups/**` и historical payload docs не являются
current authority.

## Hard Stop

При конфликте с активным каноном:

```text
CANON_CONFLICT=YES
HARD_STOP=YES
CANON_UPDATE_REQUIRED=YES
OWNER_DECISION_REQUIRED=YES
```

Если термин отсутствует в `docs/CRIPTA_GLOSSARY_RU*.md` или физически
неоднозначен — не додумывать, запросить owner decision.

## Bootstrap synchronization invariant

Точный current document set и routing определяет
`docs/DOCUMENTATION_INDEX_RU*.md`. Если меняются состав документов, пути,
mandatory pre-read или routed reading, корневые `README.md` и `AGENTS.md`
обязаны изменяться в том же documentation changeset. Устаревшая ссылка в этом
bootstrap является documentation defect.
