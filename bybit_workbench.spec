# PyInstaller recipe for the Windows x64 one-file release.
# Build only on Windows; PyInstaller does not cross-compile Windows executables.
from PyInstaller.utils.hooks import collect_submodules

hiddenimports = collect_submodules("pybit") + collect_submodules("keyring.backends") + [
    "bybit_workbench.strategies.mainnet_shadow",
]

a = Analysis(
    ["src/bybit_workbench/__main__.py"],
    pathex=["src"],
    binaries=[],
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=["scripts/release/pyinstaller_runtime_hook.py"],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="BybitStrategyWorkbench",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
)
