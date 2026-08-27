from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path

_SYMBOL_RE = re.compile(r"^[A-Z0-9]{2,32}$")


class SymbolHistory:
    """Persist a small MRU list of market symbols without storing credentials."""

    def __init__(self, path: Path, *, max_items: int = 50) -> None:
        if max_items <= 0:
            raise ValueError("max_items must be positive")
        self.path = path
        self.max_items = max_items

    def load(self) -> tuple[str, ...]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return ()
        if not isinstance(payload, dict):
            return ()
        raw_symbols = payload.get("symbols")
        if not isinstance(raw_symbols, list):
            return ()
        result: list[str] = []
        for value in raw_symbols:
            if not isinstance(value, str):
                continue
            symbol = normalize_symbol(value)
            if symbol and symbol not in result:
                result.append(symbol)
            if len(result) >= self.max_items:
                break
        return tuple(result)

    def remember(self, value: str) -> tuple[str, ...]:
        symbol = normalize_symbol(value)
        if not symbol:
            raise ValueError("symbol must contain only A-Z and 0-9")
        current = list(self.load())
        updated = [symbol, *(item for item in current if item != symbol)][: self.max_items]
        if updated != current:
            self._write(updated)
        return tuple(updated)

    def _write(self, symbols: list[str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {"schema": "symbol-history-v1", "symbols": symbols},
            ensure_ascii=False,
            indent=2,
        )
        temporary = self.path.with_suffix(f"{self.path.suffix}.tmp")
        temporary.write_text(f"{payload}\n", encoding="utf-8")
        temporary.replace(self.path)


def persistent_symbol_history(project_var_path: Path) -> SymbolHistory:
    """Use Windows LocalAppData when available and migrate the old project-local file."""

    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if not local_app_data:
        return SymbolHistory(project_var_path)
    target = Path(local_app_data) / "BybitStrategyWorkbench" / "symbol_history.json"
    if not target.exists() and project_var_path.exists():
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(project_var_path, target)
        except OSError:
            return SymbolHistory(project_var_path)
    return SymbolHistory(target)


def normalize_symbol(value: str) -> str:
    symbol = value.strip().upper()
    return symbol if _SYMBOL_RE.fullmatch(symbol) else ""
