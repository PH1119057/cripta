# CRIPTA — активный комплект документации

**Версия:** 2.1  
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

# 3. Восемь файлов ChatGPT Project Source

Для ChatGPT Project Source используется этот основной комплект:

1. `docs/CHATGPT_INTERACTION_RULES_RU.md`
2. `CRIPTA_ASSISTANT_WORK_RULES_RU_V1.md`
3. `CRIPTA_ARCHITECTURE_RULES_RU_V1.md`
4. `docs/DOCUMENTATION_INDEX_RU.md`
5. `docs/CRIPTA_GLOSSARY_RU.md`
6. `docs/CURRENT_PROJECT_MAP_RU.md`
7. `docs/TRADING_CONTOUR_RU.md`
8. `docs/OBSERVATION_ANALYTICS_RU.md`

`CHATGPT_INTERACTION_RULES_RU.md` читается ChatGPT первым.

Смысловые ссылки в Project Instructions используют семейство имени документа,
а не жёсткий номер версии или UI-суффикс.

Это оставляет свободные места Project Source для дополнительных материалов.

# 4. AGENTS.md

`AGENTS.md` остаётся в GitHub как технический bootstrap для Codex/разработчика.

Он не является восьмым обязательным файлом ChatGPT Project Source и не создаёт
самостоятельный архитектурный контракт.

# 5. Обязательный pre-read нового чата

Сначала прочитать:

1. `CHATGPT_INTERACTION_RULES_RU.md`

Затем минимальный проектный pre-read:

2. `../CRIPTA_ASSISTANT_WORK_RULES_RU_V1.md`
3. `../CRIPTA_ARCHITECTURE_RULES_RU_V1.md`
4. `DOCUMENTATION_INDEX_RU.md`
5. `CRIPTA_GLOSSARY_RU.md`
6. `CURRENT_PROJECT_MAP_RU.md`

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
3. создаётся пакет из восьми канонических файлов Project Source;
4. владелец полностью заменяет старые Project Source;
5. дополнительные временные источники добавляются только в свободные места и
   не получают статус канона автоматически.
