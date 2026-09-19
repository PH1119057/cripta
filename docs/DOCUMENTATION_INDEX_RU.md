# CRIPTA — активный комплект документации

**Версия:** 2.6
**Дата:** 2026-09-19
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
- ARCH / TRADING_CONTOUR / OBS используют одну lifecycle chain;
- MAP использует явную status matrix вместо смешения CANON/IMPLEMENTED/DEPLOYED/
  RUNTIME VERIFIED;
- GLOSSARY определяет previously ambiguous runtime/lifecycle terms и
  implementation-pass numbering.

Эта ревизия не включает real execution, не меняет Strategy records/Exchange
state и не утверждает конкретные stop/TP/H3/trailing числа.
