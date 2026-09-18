# CRIPTA — активный комплект документации

**Версия:** 2.0  
**Дата:** 2026-09-18  
**Статус:** канонический индекс документации

# 1. Цель

В проекте существует один небольшой активный комплект документов.
Никакое исследование, старый протокол, runbook или историческая схема не
считается текущим правилом только потому, что файл когда-то существовал.

# 2. Приоритет

```text
LEVEL 0 — явное текущее решение владельца

LEVEL 1 — CRIPTA_ASSISTANT_WORK_RULES_RU_V1.md
          CRIPTA_ARCHITECTURE_RULES_RU_V1.md
          docs/CRIPTA_GLOSSARY_RU.md

LEVEL 2 — docs/TRADING_CONTOUR_RU.md
          docs/OBSERVATION_ANALYTICS_RU.md

LEVEL 3 — docs/CURRENT_PROJECT_MAP_RU.md

LEVEL H — Git history, архив, старые research/evidence
```

При конфликте активных документов:

```text
HARD_STOP=YES
OWNER_DECISION_REQUIRED=YES
```

# 3. Семь файлов ChatGPT Project Source

Для ChatGPT Project Source используется ровно этот основной комплект:

1. `CRIPTA_ASSISTANT_WORK_RULES_RU_V1.md`
2. `CRIPTA_ARCHITECTURE_RULES_RU_V1.md`
3. `docs/DOCUMENTATION_INDEX_RU.md`
4. `docs/CRIPTA_GLOSSARY_RU.md`
5. `docs/CURRENT_PROJECT_MAP_RU.md`
6. `docs/TRADING_CONTOUR_RU.md`
7. `docs/OBSERVATION_ANALYTICS_RU.md`

Это оставляет свободные места Project Source для временно нужных владельцу
дополнительных материалов.

# 4. AGENTS.md

`AGENTS.md` остаётся в GitHub как технический bootstrap для Codex/разработчика.

Он не является восьмым обязательным файлом ChatGPT Project Source и не создаёт
самостоятельный архитектурный контракт.

# 5. Обязательный pre-read нового чата

Минимальный pre-read:

1. `../CRIPTA_ASSISTANT_WORK_RULES_RU_V1.md`
2. `../CRIPTA_ARCHITECTURE_RULES_RU_V1.md`
3. `DOCUMENTATION_INDEX_RU.md`
4. `CRIPTA_GLOSSARY_RU.md`
5. `CURRENT_PROJECT_MAP_RU.md`

Перед работой с торговым контуром дополнительно читать:
- `TRADING_CONTOUR_RU.md`.

Перед работой с MAYAK, Dispatcher, monitoring, Position Supervisor,
Analyst/research дополнительно читать:
- `OBSERVATION_ANALYTICS_RU.md`.

Если задача затрагивает оба контура — читать оба.

# 6. Запрет неявного использования других документов

Другие markdown-файлы в `docs/` не должны существовать в активном
документационном контуре.

Историческая документация остаётся в Git history/legacy archive.

Историю:
- не читать в стандартном pre-read;
- не использовать для генерации текущего ТЗ;
- не использовать как источник текущих параметров;
- не использовать для толкования терминов;
- открывать только по явному запросу владельца на исторический аудит,
  сравнение или воспроизводимость.

# 7. Исследования и evidence

Исследовательский файл никогда не получает статус канона автоматически.

Даже исследование, завершённое сегодня, остаётся evidence.

Если результат исследования принят владельцем, канон меняется отдельным явным
решением и обновлением соответствующей Strategy/активного документа.

# 8. Обновление Project Source в ChatGPT

После изменения активного комплекта:

1. GitHub `main` обновляется;
2. `/srv/cripta/source_checkout` синхронизируется и сверяется с `main`;
3. создаётся пакет из семи файлов Project Source;
4. владелец полностью заменяет старые Project Source;
5. дополнительные временные источники добавляются только в свободные места и
   не получают статус канона автоматически.
