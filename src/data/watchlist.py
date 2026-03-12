from __future__ import annotations

from src.config import CONFIG


class Watchlist:
    def __init__(self, symbols: list[str] | None = None):
        if symbols is not None:
            self._symbols = list(symbols)
        else:
            self._symbols = list(CONFIG.get("watchlist", {}).get("symbols", []))

    @property
    def symbols(self) -> list[str]:
        return list(self._symbols)

    def add(self, symbol: str) -> None:
        symbol = symbol.upper().strip()
        if symbol and symbol not in self._symbols:
            self._symbols.append(symbol)

    def remove(self, symbol: str) -> None:
        symbol = symbol.upper().strip()
        if symbol in self._symbols:
            self._symbols.remove(symbol)

    def contains(self, symbol: str) -> bool:
        return symbol.upper().strip() in self._symbols

    def __len__(self) -> int:
        return len(self._symbols)

    def __iter__(self):
        return iter(self._symbols)
