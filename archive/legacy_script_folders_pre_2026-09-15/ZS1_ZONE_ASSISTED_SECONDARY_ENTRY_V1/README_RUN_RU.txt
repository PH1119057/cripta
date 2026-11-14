ZS1 ZONE-ASSISTED SECONDARY ENTRY V1

1) From C:\cripta unpack this ZIP to C:\cripta.
2) Run installer:

powershell -ExecutionPolicy Bypass -File `
    .\ZS1_ZONE_ASSISTED_SECONDARY_ENTRY_V1\APPLY_ZS1_ZONE_ASSISTED_SECONDARY_ENTRY_V1.ps1

3) Run authoritative Windows gate:

powershell -ExecutionPolicy Bypass -File .\scripts\check_windows.ps1

4) Run research:

powershell -ExecutionPolicy Bypass -File `
    .\scripts\research_secondary_entry_zone_scale_zs1_windows.ps1

5) Send back the new folder (or summary.json + comparison.csv + zone_timing.csv):

C:\cripta\reports\secondary_entry_zone_scale_zs1\ALL9_ZS1_*

Downloads: DISABLED.
NEW5: NOT ACCESSED.
Entry/Exit/Risk/Execution/live: NOT CHANGED.
