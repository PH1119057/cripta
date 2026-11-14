# P53 1M Entry Displacement V1.2

Исправление runtime-ошибки V1.1 на законных минутах без сделок в frozen public-trade tape.

V1.2 не меняет исследовательский вопрос и не меняет frozen Entry. Внутренняя zero-trade minute представляется каузально: `O=H=L=C=последняя известная цена`, `volume=0`. Будущая цена не используется. Если первый доступный день начинается с минуты без сделок и предыдущая цена неизвестна, исследование по-прежнему падает fail-closed.

Дополнительно equivalence gate усилен: 1m, агрегированный обратно в 5m, должен точно воспроизвести frozen Bybit **OHLCV**, а не только OHLC.

## Установка

Из `C:\cripta`:

```powershell
Expand-Archive `
  -LiteralPath .\P53_1M_ENTRY_DISPLACEMENT_V1_2.zip `
  -DestinationPath . `
  -Force

powershell -ExecutionPolicy Bypass -File `
  .\P53_1M_ENTRY_DISPLACEMENT_V1_2\APPLY_P53_1M_ENTRY_DISPLACEMENT_V1_2.ps1
```

После `INSTALLED` и `Authoritative Windows gate: GREEN`:

```powershell
powershell -ExecutionPolicy Bypass -File `
  .\scripts\research_entry_1m_displacement_p53_windows.ps1
```

Downloads: DISABLED. NEW5/P46: NOT TOUCHED. Entry/Exit/Risk/Execution/live/UI: UNCHANGED.
