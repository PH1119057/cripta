# CRIPTA — активный комплект документации

**Версия:** 2.9
**Дата:** 2026-09-21
**Статус:** канонический индекс документации

# 1. Цель

В проекте существует один небольшой активный комплект документов.
Физическое наличие старого файла в repository/history не даёт ему authority.

# 2. Роли и приоритет

```text
META — docs/CHATGPT_INTERACTION_RULES_RU*.md

LEVEL 0 — confirmed current owner decision.

LEVEL 1 — CRIPTA_ASSISTANT_WORK_RULES_RU_*.md
          CRIPTA_ARCHITECTURE_RULES_RU_*.md
          docs/CRIPTA_GLOSSARY_RU*.md

LEVEL 2 — docs/TRADING_CONTOUR_RU*.md
          docs/OBSERVATION_ANALYTICS_RU*.md

ROUTED PROCESS / DATA CANON —
          docs/DEVELOPMENT_RELEASE_RULES_RU*.md
          docs/RESEARCH_COMPUTE_RULES_RU*.md
          docs/RESEARCH_DATA_CONTOUR_RU*.md

LEVEL 3 — docs/CURRENT_PROJECT_MAP_RU*.md

LEVEL H — Git history, archive, patch payload docs,
          old research/evidence/runbook/handoff/Pxx/EO/SE/PASS.
```

Routed process/data canon is active authority only for its task route and is not part
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

# 5. Mandatory pre-read and routed pre-read

Base new-chat pre-read:
1. CHATGPT_INTERACTION_RULES_RU*.md
2. CRIPTA_ASSISTANT_WORK_RULES_RU_*.md
3. CRIPTA_ARCHITECTURE_RULES_RU_*.md
4. DOCUMENTATION_INDEX_RU*.md
5. CRIPTA_GLOSSARY_RU*.md
6. CURRENT_PROJECT_MAP_RU*.md

Then route:
- Strategy / Entry / Exit / Execution -> TRADING_CONTOUR_RU*.md
- MAYAK / Dispatcher / Monitoring / Lifecycle Supervisor / Position Supervisor /
  Analyst -> OBSERVATION_ANALYTICS_RU*.md
- patch / Git / PostgreSQL / packaging / release / deploy / rollback ->
  DEVELOPMENT_RELEASE_RULES_RU*.md
- research / replay / OOS / holdout / large data / long compute ->
  RESEARCH_COMPUTE_RULES_RU*.md + RESEARCH_DATA_CONTOUR_RU*.md

Cross-route task -> read all relevant routed docs.

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

Routed process/data docs остаются GitHub-active canon и не входят в восьмифайловый
Project Source bundle; они читаются из verified GitHub/source mirror только по
соответствующему route.

# 10. Согласованная ревизия Strategy settings — 2026-09-19

Текущее решение владельца о Strategy-specific настройках отражено согласованно
в активном пакете:

- `CRIPTA_ARCHITECTURE_RULES_RU_*.md` — ownership policy-блоков StrategyCard,
  разделение initial protection и dynamic Exit, правила experimental version;
- `CRIPTA_ASSISTANT_WORK_RULES_RU_*.md` — authoring/fail-closed дисциплина и
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

# 12. Research data contour revision — 2026-09-21

Добавлен `docs/RESEARCH_DATA_CONTOUR_RU*.md` как routed canonical data map.

Он обязателен вместе с `RESEARCH_COMPUTE_RULES_RU*.md` для research/replay/
OOS/holdout/large-data задач и владеет:
- current 20-symbol research universe;
- physical raw/source inventory;
- contiguous coverage intervals и gaps;
- правилами historically recoverable / realtime-only data;
- exact liquidation `NO_DATA` contract;
- distinction full-universe vs subset;
- обязательной шапкой research result.

Документ не задаёт Strategy policy и не является частью восьмифайлового
every-chat Project Source bundle.
