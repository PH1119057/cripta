P46 EXIT PATH ENGINE AUDIT

Extract this archive into C:\cripta preserving directories.
All payload files are NEW files; the historical P45 module exit_break_even_v12.py is not replaced.

Run:
powershell -ExecutionPolicy Bypass -File .\scripts\research_exit_path_audit_uni_link_windows.ps1

Then run the normal project check:
powershell -ExecutionPolicy Bypass -File .\scripts\check_windows.ps1

Expected report folder:
C:\cripta\reports\exit_path_audit_v1\UNI_LINK_<timestamp>

See patches\PATCH_P46_EXIT_PATH_ENGINE_AUDIT.txt for the audit finding and expected 72h coverage.
