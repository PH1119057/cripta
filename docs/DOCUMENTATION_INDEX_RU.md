# CRIPTA — активный комплект документации

**Версия:** 3.0
**Дата:** 2026-09-26
**Статус:** канонический индекс документации

# 1. Цель

В проекте существует один небольшой активный комплект документов.
Физическое наличие старого файла в repository/history не даёт ему authority.

Вся текущая содержательная документация проекта хранится только в `docs/`.
Корневые `README.md` и `AGENTS.md` являются техническими entrypoints/bootstrap,
а не самостоятельным каноном. Расположение файла не задаёт authority: роли и
приоритет определяет только этот INDEX.

# 2. Роли и приоритет

```text
META — docs/CHATGPT_INTERACTION_RULES_RU*.md

LEVEL 0 — confirmed current owner decision.

LEVEL 1 — docs/CRIPTA_ASSISTANT_WORK_RULES_RU_*.md
          docs/CRIPTA_ARCHITECTURE_RULES_RU_*.md
          docs/CRIPTA_GLOSSARY_RU*.md

LEVEL 2 — docs/TRADING_CONTOUR_RU*.md
          docs/OBSERVATION_ANALYTICS_RU*.md

ROUTED PROCESS CANON —
          docs/DEVELOPMENT_RELEASE_RULES_RU*.md
          docs/RESEARCH_COMPUTE_RULES_RU*.md

TECHNICAL SECURITY BASELINE — docs/SECURITY.md
          применяется ко всей работе, но не задаёт trading policy.

LEVEL 3 — docs/CURRENT_PROJECT_MAP_RU*.md

LEVEL H — Git history, archive, patch payload docs,
          old research/evidence/runbook/handoff/Pxx/EO/SE/PASS.
```

Routed process canon is active authority only for its task route and is not part
of the mandatory every-chat Project Source bundle.

If owner decision conflicts with canon:

```text
CANON_CONFLICT=YES
HARD_STOP=YES
CANON_UPDATE_REQUIRED=YES
OWNER_DECISION_REQUIRED=YES
```

# 3. Восемь файлов ChatGPT Project Source

1. `docs/CHATGPT_INTERACTION_RULES_RU*.md`
2. `docs/CRIPTA_ASSISTANT_WORK_RULES_RU_*.md`
3. `docs/CRIPTA_ARCHITECTURE_RULES_RU_*.md`
4. `docs/DOCUMENTATION_INDEX_RU*.md`
5. `docs/CRIPTA_GLOSSARY_RU*.md`
6. `docs/CURRENT_PROJECT_MAP_RU*.md`
7. `docs/TRADING_CONTOUR_RU*.md`
8. `docs/OBSERVATION_ANALYTICS_RU*.md`

Используется семейство имени, а не номер версии/UI suffix.
`CHATGPT_INTERACTION_RULES_RU*.md` читается первым.

# 4. Корневые entrypoints

`README.md` и `AGENTS.md` — единственные текущие Markdown-документы, которые
остаются в корне repository.

- `README.md` — короткий человекочитаемый вход в проект и ссылки на current docs;
- `AGENTS.md` — GitHub-only bootstrap для Codex/разработчика/робота.

Они не создают самостоятельный архитектурный или торговый контракт. Если
меняются состав current docs, их пути, mandatory pre-read или routed reading,
`README.md` и `AGENTS.md` обязаны быть обновлены в том же changeset, что и
этот INDEX. Устаревшая ссылка в root bootstrap является documentation defect.

# 5. Mandatory pre-read and routed pre-read

Base new-chat pre-read:
1. docs/CHATGPT_INTERACTION_RULES_RU*.md
2. docs/CRIPTA_ASSISTANT_WORK_RULES_RU_*.md
3. docs/CRIPTA_ARCHITECTURE_RULES_RU_*.md
4. docs/DOCUMENTATION_INDEX_RU*.md
5. docs/CRIPTA_GLOSSARY_RU*.md
6. docs/CURRENT_PROJECT_MAP_RU*.md

Then route:
- Strategy / Entry / Exit / Execution -> docs/TRADING_CONTOUR_RU*.md
- MAYAK / Dispatcher / Monitoring / Lifecycle Supervisor / Position Supervisor /
  Analyst -> docs/OBSERVATION_ANALYTICS_RU*.md
- patch / Git / PostgreSQL / packaging / release / deploy / rollback ->
  docs/DEVELOPMENT_RELEASE_RULES_RU*.md
- research / replay / OOS / holdout / large data / long compute ->
  docs/RESEARCH_COMPUTE_RULES_RU*.md

Cross-route task -> read all relevant routed docs.

# 6. Историческая изоляция

В активном каталоге `docs/` находится только текущая содержательная документация и technical baseline; historical docs туда не возвращаются.

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
2. существующим механизмом синхронизируется `/srv/cripta/source_checkout`;
   локальное зеркало владельца также получает GitHub `main` своим штатным sync-механизмом;
3. формируется набор восьми текущих Project Source файлов из `docs/`;
4. root `README.md` и `AGENTS.md` сверяются с current paths/pre-read/routing;
5. владелец полностью заменяет старые Project Source;
6. дополнительные материалы не получают authority автоматически.

Routed process docs остаются GitHub-active canon и не входят в восьмифайловый
Project Source bundle; они читаются из verified GitHub/source mirror только по
соответствующему route.

# 10. Согласованная ревизия Strategy settings — 2026-09-19

Текущее решение владельца о Strategy-specific настройках отражено согласованно
в активном пакете:

- `docs/CRIPTA_ARCHITECTURE_RULES_RU_*.md` — ownership policy-блоков StrategyCard,
  разделение initial protection и dynamic Exit, правила experimental version;
- `docs/CRIPTA_ASSISTANT_WORK_RULES_RU_*.md` — authoring/fail-closed дисциплина и
  запрет research/history как trading default;
- `docs/CRIPTA_GLOSSARY_RU*.md` — термины Strategy settings, initial protection,
  Strategy Candidate/Draft и experimental Strategy version;
- `docs/TRADING_CONTOUR_RU*.md` — точное распределение настроек по policy-
  блокам и различие protective envelope / dynamic Exit;
- `docs/CURRENT_PROJECT_MAP_RU*.md` — текущий implementation status authoring.

В этой ревизии не утверждается конкретный канонический Exit и не закрепляются
исследовательские числа как global defaults. H3/touch/break-even/trailing и
временные protective boundaries становятся торговой policy только внутри exact
owner-approved Strategy version.

В рамках именно ревизии Strategy settings META routing и наблюдательный контур
не менялись. Последующая ревизия §11 обновляет META source-authority wording и
Lifecycle Supervisor contract, не передавая наблюдательному контуру торговые права.

# 11. Ревизия lifecycle / one-way / release contract — 2026-09-19

Owner-approved revision синхронизирует активный пакет по следующим инвариантам:

- GitHub `main` — единственный authority; `source_checkout` — synchronized
  operational mirror;
- release order един: test/overlay -> exact commit -> GitHub/remote verify ->
  backup -> deploy exact commit -> runtime evidence;
- независимые Strategy могут иметь противоположные signals, но текущий Bybit
  one-way physical position slot имеет только одного real lifecycle owner;
- reservation входит в формирование EntryDecision; `ACCEPTED` существует
  только после successful reservation;
- для real Strategy обязателен owner-approved initial loss-containment;
- emergency execution capability не является policy; автоматическое аварийное
  действие требует exact Strategy emergency/protection-failure contract;
- Strategy deactivation не меняет ExitPlan уже открытой StrategyPosition;
- lifecycle chain определяется в ARCH §9.1; TRADING_CONTOUR/OBS ссылаются на неё
  вместо дублирования;
- MAP использует явную status matrix и разносит runtime LIVENESS / BEHAVIOR
  вместо одного неоднозначного RUNTIME VERIFIED;
- GLOSSARY определяет previously ambiguous runtime/lifecycle terms и
  implementation-pass numbering.

Эта ревизия не включает real execution, не меняет Strategy records/Exchange
state и не утверждает конкретные stop/TP/H3/trailing числа.

# 12. Revision: slot admission / runtime evidence / routed process canon — 2026-09-19

Owner decision adds:
- single token->entity authority in GLOSSARY;
- EXCHANGE_POSITION_OWNERSHIP_CONFLICT as EntryDecision outcome, not critical fault;
- EXCHANGE_POSITION_OWNERSHIP_INVARIANT_BROKEN as separate lifecycle fault;
- durable physical slot claim before ACCEPTED;
- slot claim + capital reservation as one all-or-nothing admission contract;
- fresh position-mode/positionIdx required state;
- EXCHANGE_POSITION_MODE_MISMATCH fail-closed contract;
- same-symbol hedge unsupported in current one-way contract;
- critical fault durable owner-notification delivery;
- single lifecycle definition in ARCH §9.1; TC/OBS reference it;
- runtime evidence split into LIVENESS and BEHAVIOR;
- explicit LIVE-arm checklist;
- Git-first package MANIFEST bound to release_commit;
- deploy-host GitHub write credential not required by default;
- WORK split into small core + routed DEVELOPMENT_RELEASE / RESEARCH_COMPUTE docs;
- public-repository full-history secret scan becomes security gate.

This revision is DOCUMENTATION/CANON only. New slot/mode/fault-delivery requirements
are not declared IMPLEMENTED/DEPLOYED/RUNTIME BEHAVIOR VERIFIED until code/DB/
runtime are separately audited and changed.

# 13. Implementation / runtime sync — 2026-09-21

После documentation-first канона 2026-09-19 выполнены implementation passes и
runtime verification. Текущий authoritative implementation checkpoint отражён в
`docs/CURRENT_PROJECT_MAP_RU*.md`.

CHECKED HERE:
- durable physical slot claim + atomic capital reservation implemented/deployed;
- fresh position-mode state contract implemented/deployed and fail-closed;
- canonical request-state lifecycle implemented/deployed;
- exact StrategyPosition slot/reservation binding implemented/deployed;
- Lifecycle Supervisor new invariant faults implemented/deployed;
- critical fault durable delivery/retry/ack/escalation implemented/deployed;
- LIVE-arm evidence/session gate implemented/deployed;
- Git-first release identity and exact installed/loaded commit contract implemented;
- controlled PostgreSQL behavior verification completed;
- current mainnet remains disarmed.

This sync does not authorize MICRO_LIVE or LIVE. Current real-arm blockers and
the exact runtime checkpoint are owned by CURRENT_PROJECT_MAP and
TRADING_CONTOUR §4.7.

# 14. Geometry / sync / SentinelX revision — 2026-09-25

Owner-confirmed: H9/H3 baseline формализован; Strategy-owned GeometrySpec может иметь shared causal Geometry timeline; research различает repeated touches и unique H9 episodes. GitHub main остаётся publication authority, server mirror и локальное зеркало владельца получают изменения штатными sync-механизмами. SentinelX является текущим ChatGPT server-management rail; потеря tool connection не равна server/job failure, требуется reconnect + host-state verification. PostgreSQL actor/interpreter/role smoke обязателен до DB-sensitive work. Geometry-timeline implementation этой документационной ревизией не объявляется DEPLOYED.

# 15. Documentation topology / root bootstrap revision — 2026-09-26

OWNER DECISION:
- вся текущая содержательная документация CRIPTA хранится в `docs/`;
- корневые `README.md` и `AGENTS.md` остаются только стабильными entrypoints;
- WORK, ARCH и SECURITY перенесены из root в `docs/` без изменения их authority;
- изменение состава документов, путей, mandatory pre-read или routed reading
  требует синхронного обновления INDEX + root README + root AGENTS;
- routed DEVELOPMENT_RELEASE и RESEARCH_COMPUTE имеют локальную непрерывную
  нумерацию разделов; старая разделённая сквозная нумерация удалена;
- MAP §18 зеркалит все обязательные имена LIVE-arm gates из TRADING_CONTOUR §4.7.

Эта ревизия не меняет trading behavior, Strategy ownership или runtime rights.
Неоднозначности Strategy Candidate/monitoring Strategy и физического owner
Geometry timeline этой ревизией не фиксируются и остаются без изменения.
