# CRIPTA

CRIPTA — production-платформа для причинного наблюдения рынка, торговых
Strategy, исполнения, сопровождения позиций и воспроизводимой аналитики.

## Документация

Вся текущая содержательная документация проекта хранится в `docs/`.
Единственный authority по составу, ролям и pre-read:

- [docs/DOCUMENTATION_INDEX_RU.md](docs/DOCUMENTATION_INDEX_RU.md)

Базовый pre-read нового чата / исполнителя:

1. [docs/CHATGPT_INTERACTION_RULES_RU.md](docs/CHATGPT_INTERACTION_RULES_RU.md)
2. [docs/CRIPTA_ASSISTANT_WORK_RULES_RU_V1.md](docs/CRIPTA_ASSISTANT_WORK_RULES_RU_V1.md)
3. [docs/CRIPTA_ARCHITECTURE_RULES_RU_V1.md](docs/CRIPTA_ARCHITECTURE_RULES_RU_V1.md)
4. [docs/DOCUMENTATION_INDEX_RU.md](docs/DOCUMENTATION_INDEX_RU.md)
5. [docs/CRIPTA_GLOSSARY_RU.md](docs/CRIPTA_GLOSSARY_RU.md)
6. [docs/CURRENT_PROJECT_MAP_RU.md](docs/CURRENT_PROJECT_MAP_RU.md)
7. [docs/SECURITY.md](docs/SECURITY.md)

Все 11 current docs должны быть доступны в Project Source; mandatory reading
определяется base pre-read + task route.

Current ChatGPT UI constraints (owner-checked 2026-09-28):
Project Instructions <= 8000 символов; Project Source <= 12 files.
Текущий bundle = 11. Документы не дробятся так, чтобы расходовать/превышать
этот лимит; 12-й current file требует owner decision.

Routed documents:

- [docs/TRADING_CONTOUR_RU.md](docs/TRADING_CONTOUR_RU.md) — Strategy / Entry / Exit / Execution;
- [docs/OBSERVATION_ANALYTICS_RU.md](docs/OBSERVATION_ANALYTICS_RU.md) — MAYAK / Dispatcher / Monitoring / Supervisor / Analyst;
- [docs/DEVELOPMENT_RELEASE_RULES_RU.md](docs/DEVELOPMENT_RELEASE_RULES_RU.md) — patch / Git / PostgreSQL / release / deploy;
- [docs/RESEARCH_COMPUTE_RULES_RU.md](docs/RESEARCH_COMPUTE_RULES_RU.md) — research / replay / OOS / data forensic / compute;

Любой server-side writer/effective-actor change дополнительно маршрутизируется
через DEVELOPMENT_RELEASE §5.1 + §19.1–19.4 и MAP §1.4 независимо от primary route.
Exact UI Project Instructions template хранится вне Project Source:
`operations/bootstrap/CHATGPT_PROJECT_INSTRUCTIONS_RU.txt`.

## Source of truth

```text
AUTHORITATIVE: GitHub PH1119057/cripta:main
OPERATIONAL MIRROR: /srv/cripta/source_checkout
```

Installed runtime, PostgreSQL и Exchange truth проверяются отдельно.

## Root bootstrap invariant

`README.md` и `AGENTS.md` — единственные текущие Markdown-entrypoints в
корне repository. Они не являются отдельным каноном.

Если меняются состав current docs, их пути, mandatory pre-read или routed
reading, этот README и `AGENTS.md` обязаны обновляться в том же changeset, что
и `docs/DOCUMENTATION_INDEX_RU.md`.

Постоянные правила ведения документации (нумерация/ссылки, dated MAP
checkpoints, release/runtime identity, mirrored canonical lists, current server
operational profile и open architecture decisions) находятся в
`docs/DOCUMENTATION_INDEX_RU.md §17`.

Для server-side work текущие non-secret actors/write-roots/prohibitions
зафиксированы в `docs/CURRENT_PROJECT_MAP_RU.md §1.4`; постоянный permission
contract — в `docs/DEVELOPMENT_RELEASE_RULES_RU.md §5.1 и §19.1–19.4`.
