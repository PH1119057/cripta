# ENTRY V1 — контракт области действия failure embargo

**Версия:** 1.0  
**Дата:** 2026-08-31  
**Дефект:** `ENTRY_V1_FAILURE_EMBARGO_SCOPE_BUG`

## Причинная граница

Исследовательская траектория exact-touch candidate не является производственным сигналом.
Она может сохранять MFE/MAE и будущие ценовые milestones, но не имеет права менять
состояние, которое разрешает или запрещает следующие реальные Entry.

Шестидесятиминутный failure embargo может быть создан только траекторией, которая
прошла Core gate и сформировала действительный `CORE_SIGNAL`. Текущий SHADOW runtime
не имеет достоверной границы order/fill, поэтому перенос embargo на order или fill
без отдельного исследования запрещён.

## Persistence

После restart сохраняется только разрешённое производственное поле
`failure_embargo_until`. Research outcomes, rejected candidates и их milestones
не восстанавливаются как live enforcement state.

## Что не меняется

- длительность подтверждённого embargo остаётся 60 минут;
- исследовательское наблюдение rejected candidates продолжается;
- Core gate, M3 setup и торговое исполнение не меняются;
- это исправление не относится к Mayak, Dispatcher, Exit, Risk или Supervisor.
