ZS1 ZONE-ASSISTED SECONDARY ENTRY V1.1 RUFF FIX

V1 failed closed before changing C:\cripta. Do not manually edit anything.

1) From C:\cripta unpack this ZIP to C:\cripta.

2) Run installer:

powershell -ExecutionPolicy Bypass -File `
    .\ZS1_ZONE_ASSISTED_SECONDARY_ENTRY_V1_1_RUFF_FIX\APPLY_ZS1_ZONE_ASSISTED_SECONDARY_ENTRY_V1_1_RUFF_FIX.ps1

3) Run authoritative Windows gate:

powershell -ExecutionPolicy Bypass -File .\scripts\check_windows.ps1

4) Only if the gate is green, run research:

powershell -ExecutionPolicy Bypass -File `
    .\scripts\research_secondary_entry_zone_scale_zs1_windows.ps1

5) Send back the new folder or at least:
summary.json
comparison.csv
zone_timing.csv
by_symbol.csv
by_month.csv
p52_direct_structure_quality.csv

Output:
C:\cripta\reports\secondary_entry_zone_scale_zs1\ALL9_ZS1_*

Downloads: DISABLED.
NEW5: NOT ACCESSED.
Entry/Exit/Risk/Execution/live: NOT CHANGED.
