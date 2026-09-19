# CRIPTA — bootstrap для исполнителя

Для ChatGPT первым читать `docs/CHATGPT_INTERACTION_RULES_RU*.md`.

Перед любой работой по проекту читать актуальный активный канон по семействам:

1. `CRIPTA_ASSISTANT_WORK_RULES_RU_*.md`
2. `CRIPTA_ARCHITECTURE_RULES_RU_*.md`
3. `docs/DOCUMENTATION_INDEX_RU*.md`
4. `docs/CRIPTA_GLOSSARY_RU*.md`
5. `docs/CURRENT_PROJECT_MAP_RU*.md`

При работе с конкретным слоем дополнительно читать его active routed document
из `docs/DOCUMENTATION_INDEX_RU*.md`.

Перед patch/Git/PostgreSQL/package/release/deploy читать
`docs/DEVELOPMENT_RELEASE_RULES_RU*.md`.

Перед research/replay/OOS/holdout/large-data/long-compute читать
`docs/RESEARCH_COMPUTE_RULES_RU*.md`.

## Source of truth

```text
AUTHORITATIVE: GitHub PH1119057/cripta:main
OPERATIONAL MIRROR: /srv/cripta/source_checkout
```

Operational mirror обязан быть синхронизирован с GitHub `main`; при конфликте
authority имеет GitHub. Project Source, старые ZIP/чаты, локальные копии, Git
history, historical research/patch docs и archive не являются source of truth.

## Hard Stop

При конфликте с активным каноном:

```text
CANON_CONFLICT=YES
HARD_STOP=YES
CANON_UPDATE_REQUIRED=YES
OWNER_DECISION_REQUIRED=YES
```

Не менять архитектуру и торговый смысл по собственной инициативе.
После подтверждения владельца сначала обновить канон, затем реализацию.

## Терминологический Hard Stop

Если термин отсутствует в `docs/CRIPTA_GLOSSARY_RU*.md` либо допускает
несколько трактовок, не выбирать смысл самостоятельно.

## Исследования

Исследование любой давности является evidence, а не каноном.

## Историческая изоляция

По умолчанию не читать и не включать в широкий поиск текущего канона:
- `archive/**`
- `patch_backups/**`
- historical `*/payload/docs/**`
- старые Pxx / EO / SE / PASS / runbook / handoff
- Git history

Открывать их только по явной исторической задаче.
