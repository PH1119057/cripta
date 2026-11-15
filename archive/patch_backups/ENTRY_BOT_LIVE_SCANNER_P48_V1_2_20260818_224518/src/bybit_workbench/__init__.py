"""Bybit Strategy Workbench."""

__version__ = "0.8.5"
__patch__ = "P41"


def display_version() -> str:
    """Human-readable build identifier shown in the desktop UI."""

    return f"v{__version__} · {__patch__}"
