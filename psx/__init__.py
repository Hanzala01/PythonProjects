"""
psx — free Pakistan Stock Exchange data for CELESX.

    from psx import history, quote, movers

    quote("OGDC", "LUCK", "HBL")
    history("OGDC", days=90)
    movers("KSE100", top=5)

Everything here comes from PSX's own public portal by way of `psxdata`.
It is free, needs no key and no account — and in exchange it is end-of-day,
has no order book and no intraday bars. `psx.data` documents exactly where
those limits fall.
"""

from .data import (
    history,
    index,
    movers,
    quote,
    screener,
    sectors,
    symbols,
)

__all__ = [
    "history",
    "index",
    "movers",
    "quote",
    "screener",
    "sectors",
    "symbols",
]
