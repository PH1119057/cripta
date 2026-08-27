# P53 1M Entry Displacement V1.3

Baseline: `bybit-workbench 0.8.5 / P48.2 + accepted P53 V1.2`.

Research only. Downloads: **DISABLED**. Entry / Exit / Risk / Execution / live / UI: **UNCHANGED**. P46/NEW5: **NOT TOUCHED**.

## Почему нужен V1.3

V1.2 дошёл до реального exact 1m→5m OHLCV gate и обнаружил систематическую несовместимость materialization: объёмы и closes совпадали, но `open` и иногда `high/low` отличались на один price tick. Причина — P53 использовал первую сделку минуты как `open`, тогда как frozen 5m требует continuous previous-close opening semantics.

V1.3 **не ослабляет gate** и не вводит tolerance. После первой каузально известной цены каждая 1m свеча открывается по предыдущему close; high/low включают этот opening point и реальные trade extrema. Zero-trade minutes остаются flat previous-close bars с volume 0. Cache version повышена. После этого по-прежнему требуется exact 1m→5m OHLCV equality.

## Установка

Из `C:\cripta`:

```powershell
Expand-Archive `
    -LiteralPath .\P53_1M_ENTRY_DISPLACEMENT_V1_3.zip `
    -DestinationPath . `
    -Force

powershell -ExecutionPolicy Bypass -File `
    .\P53_1M_ENTRY_DISPLACEMENT_V1_3\APPLY_P53_1M_ENTRY_DISPLACEMENT_V1_3.ps1
```

Installer: baseline SHA -> payload SHA -> temp overlay -> PowerShell syntax/ASCII -> py_compile -> Ruff -> mypy -> targeted pytest -> broad overlay pytest -> apply -> authoritative `scripts\check_windows.ps1`. При failure до apply реальный проект не меняется; после apply failure выполняется rollback.

## После GREEN

```powershell
powershell -ExecutionPolicy Bypass -File `
    .\scripts\research_entry_1m_displacement_p53_windows.ps1
```

Существующий V1.2 cache удалять не нужно: V1.3 имеет новый cache version и несовместимый cache не переиспользует.
