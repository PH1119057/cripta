# P45.1 — Clean Zone Lifecycle

Цель этапа — исправить главный дефект P45: одна физическая зона не должна бесконечно
накапливать десятки ретестов и многократно менять роль без сброса жизненного цикла.

## Замороженная геометрия

P45.1 сохраняет исходную каузальную геометрию P45:

- 15m pivot 2+2;
- Wilder ATR(200);
- half-width зоны = 0.50 ATR;
- новый независимый тест разрешается только после ухода цены на 1.00 ATR;
- подтверждённый пробой = 2 последовательных close за дальней границей зоны;
- на exact `touch_at` доступны только уже закрытые 15m свечи.

## Новый lifecycle

Физический уровень состоит из фаз.

1. Pivot создаёт новую support/resistance phase.
2. Новые pivot могут объединяться только с **активной фазой той же роли**.
3. Подтверждённый пробой завершает текущую фазу.
4. На том же физическом уровне создаётся новая противоположная role-reversal phase.
5. Счётчик независимых тестов новой фазы начинается с нуля.
6. Фаза в любом случае истекает через 168 часов (7 дней). Этот порог уже был
   зафиксирован в P45 как граница `old >= 7d`, поэтому P45.1 не подбирает его по результатам.
7. Entry получает номер теста только если уровень действительно re-armed после предыдущего
   теста. Если рынок всё ещё трётся об уровень, это `not_rearmed`, а не следующий тест.

Так можно честно сравнить `1st → 2nd → 3rd → 4th+`, а после пробоя отдельно исследовать
`first retest after break`.

## Bounce / break / false break

Для каждого независимого 15m теста P45.1 смотрит следующие 96 свечей = 24 часа и
классифицирует первое структурное событие:

- `bounce` — close ушёл от зоны в правильную сторону минимум на 1 ATR;
- `clean_break` — два последовательных close подтвердили пробой;
- `false_break_reclaim` — был один close за зоной, но следующий close вернулся обратно;
- `unresolved` — ничего из этого не произошло за 24 часа.

## Fresh approach

Сохраняются найденные P45 признаки:

- `near_zone_fraction_2h`;
- `approach_slope_atr_per_bar`;
- `approach_distance_range_atr`.

Для них S1 используется только для квартильных порогов, а S2+S3 — для transfer-анализа.
Комбинация Q1 slope + Q1 time-near сохраняется как discovery-кандидат, а не как подтверждённый
live-фильтр.

P44 BTC-relative residual подключается только как exploratory interaction.

## Выходные файлы

Основные:

- `core_lifecycle_features.csv`;
- `phase_catalog.csv`;
- `independent_zone_touch_outcomes.csv`;
- `core_touch_ordinal_matrix.csv`;
- `zone_touch_outcome_matrix.csv`;
- `cross_asset_touch_outcome_transfer.csv`;
- `asset_rule_matrix.csv` и `cross_asset_rule_transfer.csv`;
- `segment_rule_matrix.csv`;
- `direction_rule_matrix.csv`;
- `s1_feature_thresholds.csv`;
- `feature_quartiles_oos.csv`;
- `fresh_approach_combo_oos.csv`;
- `legacy_p45_lifecycle_comparison.csv`;
- `summary.md/json` и `RUN_COMPLETE.json`.

## Guardrail

P45.1 остаётся discovery на уже просмотренных 90 днях. Любой кандидат, который получится
из P45/P45.1/P44, должен быть заморожен до нового временного OOS периода. Скрипт не меняет
live trading, Exit, Risk, leverage или execution и ничего не скачивает из сети.
