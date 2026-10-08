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

Owner-controlled real execution smoke-test is an explicit operator diagnostic,
not Strategy behavior. Exact contract: `TRADING_CONTOUR §4.8`, architecture
boundary: `ARCH §7.1`.

Current R1 Entry-time exchange protection contract: catastrophic SL `-10%` +
price-based TP at the causal opposite current L5-3 inner boundary; subsequent TP
replacement belongs to Universal Exit. Exact owner document:
`docs/TRADING_CONTOUR_RU.md` Protection and activation boundary.

PAPER/REAL is one Strategy/Entry/Exit lifecycle with mutually exclusive
execution environments, not two trading implementations. Current contract:
`ARCH §7.2`, `TRADING_CONTOUR §4.9`, `GLOSSARY §11`; real arm additionally
requires `PAPER_REAL_DECISION_PARITY=PASS`.

Dashboard presentation/read-model имеет отдельный verified release identity
`DASHBOARD_UI_COMMIT` и не требует остановки trading runtime. Static assets и
строго read-only projections/aggregations могут обновляться независимо; backend
read-model change перезапускает только Dashboard. Auth/control/mutation или
trading-decision changes в этот scope не входят. Exact rule:
`DEVELOPMENT_RELEASE §8.1`.



## Filesystem contours

Target physical separation is canonical:

```text
SOURCE   /srv/cripta/source_checkout
RUNTIME  /srv/cripta/runtime
RESEARCH /data/cripta/research
ARCHIVE  /data/cripta/script_archive
```

Research writes live on the data disk. Runtime must not import/execute directly
from research. Exact contract: `DEVELOPMENT_RELEASE §42`,
`RESEARCH_COMPUTE §17`; current migration state: `CURRENT_PROJECT_MAP §1.5`.


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
## Architecture capability guardrail

При architecture-sensitive change запрещено молча удалять существующую
capability вместе с неправильным owner. Если replacement owner/contract/
implementation не доказаны, действует ARCHITECTURE_CAPABILITY_GAP=YES +
HARD_STOP=YES. Exact contract: META §15, WORK §4.3, ARCH §13.

## Routine restart recovery

The canonical recovery requirements are defined in ARCH, TRADING_CONTOUR, GLOSSARY, DEVELOPMENT_RELEASE and DOCUMENTATION_INDEX. Exchange snapshots are authoritative for current positions, while durable local history must be preserved. A canonical decision is not deployed implementation.
