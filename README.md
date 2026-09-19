# CRIPTA

CRIPTA — production-платформа для причинного наблюдения рынка, торговых
Strategy, исполнения, сопровождения позиций и воспроизводимой аналитики.

## Канонический вход

Документы идентифицируются по семейству имени, а не по номеру версии:

1. `CHATGPT_INTERACTION_RULES_RU*.md`
2. `CRIPTA_ASSISTANT_WORK_RULES_RU_*.md`
3. `CRIPTA_ARCHITECTURE_RULES_RU_*.md`
4. `DOCUMENTATION_INDEX_RU*.md`
5. `CRIPTA_GLOSSARY_RU*.md`
6. `CURRENT_PROJECT_MAP_RU*.md`
7. `TRADING_CONTOUR_RU*.md`
8. `OBSERVATION_ANALYTICS_RU*.md`

Точный текущий состав и pre-read определяет
[docs/DOCUMENTATION_INDEX_RU.md](docs/DOCUMENTATION_INDEX_RU.md).

## Source of truth

```text
AUTHORITATIVE: GitHub PH1119057/cripta:main
OPERATIONAL MIRROR: /srv/cripta/source_checkout
```

Operational mirror должен быть синхронизирован с verified GitHub ref, но не
является вторым authority.

Installed runtime, PostgreSQL и Exchange truth проверяются отдельно.
Project Source, память ChatGPT, старые ZIP/чаты и `C:\cripta` не являются
source of truth.

## Routed process canon

Базовый Project Source остаётся восьмифайловым. Дополнительные active docs
читаются только по task route:

- `docs/DEVELOPMENT_RELEASE_RULES_RU.md` — patch/Git/PostgreSQL/release/deploy;
- `docs/RESEARCH_COMPUTE_RULES_RU.md` — research/compute/data.

## Верхняя архитектура

```text
MAYAK
  ↓
DISPATCHER
  ↓
STRATEGY
 ├─ ENTRY
 └─ EXIT
  ↓
EXECUTION
  ↓
EXCHANGE
```

Подробности не дублируются в README.

## История

Старые самостоятельные концептуальные/Workbench/PASS документы находятся в
`archive/**` либо Git history.

Historical docs внутри старых patch/research artifacts могут оставаться на
месте ради воспроизводимости, но не являются текущей инструкцией.

## Security

Базовые security-инварианты: [SECURITY.md](SECURITY.md).
