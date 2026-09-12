#!/usr/bin/env python3
"""
psx.data — Pakistan Stock Exchange data, normalised.

This is a thin layer over `psxdata`, which scrapes PSX's own public data
portal (dps.psx.com.pk). The library works; what it returns needs cleaning
before anything numeric can be done with it, and every fix below is here
because the raw output was measured, not because it looked wrong.

WHAT THE RAW LIBRARY HANDS BACK

    stocks()     rows in REVERSE date order, and it says so on stderr —
                 "Dates are not in chronological order". A rolling mean or
                 a pct_change over that is backwards and silently wrong.

    indices()    every price-ish column is a STRING as scraped from the
                 page: change_pct is '0.41%', volume is '54,997'. Sorting
                 by volume puts '9,000' above '54,997' because it is
                 comparing text.

    screener()   sector is a bare float code — 820.0, not a name. The
                 sector NAMES live in a different call, sectors(), keyed
                 '0820'. Joining the two is the only way to get a readable
                 sector onto a symbol.

    screener()   market_cap and free_float are present but empty, and
                 counting them is the only way to see it. Over 745 rows:
                 market_cap had 76 non-null values and every one of them
                 was 0.0; free_float had 85 non-null and exactly ONE that
                 was not zero. A column that is 99.9% nothing will still
                 sort, still average, and still look like data — so these
                 are dropped by default, with the names recorded in
                 frame.attrs["dropped_sparse"] so the drop is visible
                 rather than silent. Pass keep_sparse=True to see them.

WHAT THIS FREE ROUTE CANNOT DO, AT ALL

    No intraday bars. PSX does not publish a 1/5/15-minute archive, so no
    scraper can produce one — this is a limit of the source, not of the
    library, and no amount of code here changes it.

    No order-book depth, and nothing is real-time. Quotes are the portal's
    snapshot, which lags the live market.

    Commercial redistribution of this data needs a licence from PSX.
    Personal research and your own bot are fine; reselling it is not.
"""

from __future__ import annotations

import datetime as _dt
import logging

import pandas as pd
import psxdata


# ── one warning, silenced on purpose ──────────────────────────────────────
# psxdata logs "Dates are not in chronological order" whenever a history
# frame comes back reversed, which is every time. history() below sorts it
# immediately, so by the time the caller sees the frame the warning is not
# just noise — it contradicts what was handed over, and a warning that is
# wrong teaches people to ignore warnings.
#
# Only this exact message is dropped. Anything else psxdata logs still comes
# through, which is why this is a filter and not a level change.
class _ChronologyNoise(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return "chronological order" not in record.getMessage()


logging.getLogger("psxdata").addFilter(_ChronologyNoise())
for _name in list(logging.root.manager.loggerDict):
    if _name.startswith("psxdata"):
        logging.getLogger(_name).addFilter(_ChronologyNoise())

__all__ = [
    "history",
    "quote",
    "screener",
    "index",
    "sectors",
    "symbols",
    "movers",
]


# ── turning scraped text back into numbers ────────────────────────────────
# The portal renders numbers for humans, so they arrive with the thousands
# separator and the percent sign still attached. Stripping those characters
# and coercing is enough; anything that does not survive the coercion was
# not a number to begin with and becomes NaN rather than raising, because a
# single unparseable cell should not cost the whole table.
def _numeric(series: pd.Series) -> pd.Series:
    if series.dtype.kind in "ifb":
        return series
    cleaned = series.astype("string").str.replace(r"[,%\s]", "", regex=True)
    return pd.to_numeric(cleaned, errors="coerce")


def _numeric_columns(frame: pd.DataFrame, columns) -> pd.DataFrame:
    for column in columns:
        if column in frame.columns:
            frame[column] = _numeric(frame[column])
    return frame


def history(
    symbol: str,
    start: _dt.date | str | None = None,
    end: _dt.date | str | None = None,
    days: int | None = None,
    drop_anomalies: bool = False,
) -> pd.DataFrame:
    """
    Daily OHLCV for one symbol, oldest row first.

    Give either an explicit `start`/`end` or a `days` lookback. The frame is
    sorted ascending and re-indexed, which is the whole point of this
    wrapper — see the module docstring.

    `is_anomaly` is the library's own flag for a bar it considers suspect.
    It is kept as a column so you can see what was marked; pass
    drop_anomalies=True to remove those rows instead.
    """
    end = _as_date(end) or _dt.date.today()
    if start is None:
        start = end - _dt.timedelta(days=days if days is not None else 365)
    else:
        start = _as_date(start)

    frame = psxdata.stocks(symbol, start=start, end=end)
    if frame.empty:
        return frame

    frame = frame.sort_values("date").reset_index(drop=True)
    if drop_anomalies and "is_anomaly" in frame.columns:
        frame = frame[~frame["is_anomaly"].astype(bool)].reset_index(drop=True)
    return frame


def quote(*symbols: str) -> pd.DataFrame:
    """
    Latest portal snapshot for one or more symbols, sector name attached.

    Not real-time — this is the screener row for each symbol, which is what
    the free route has. One scrape serves any number of symbols, so asking
    for twenty costs the same as asking for one.
    """
    if not symbols:
        raise ValueError("quote() needs at least one symbol")

    wanted = {s.strip().upper() for s in symbols}
    table = screener()
    found = table[table["symbol"].str.upper().isin(wanted)].reset_index(drop=True)

    missing = wanted - set(found["symbol"].str.upper())
    if missing:
        # Worth saying out loud: a typo and a delisted symbol look identical
        # from here, and silently returning fewer rows than asked for is how
        # a bot ends up trading a portfolio it thinks is complete.
        print(f"psx: no screener row for {', '.join(sorted(missing))}")
    return found


def screener(keep_sparse: bool = False, min_useful: float = 0.01) -> pd.DataFrame:
    """
    The full PSX screener — every listed symbol — with sector names joined on.

    Columns carrying a usable (non-null, non-zero) value in fewer than
    `min_useful` of rows are dropped, because on this feed market_cap and
    free_float are empty in a way that still looks numeric. The dropped
    names land in frame.attrs["dropped_sparse"]; keep_sparse=True keeps
    them. The threshold is a ratio, so if PSX starts populating these the
    columns come back on their own.
    """
    frame = psxdata.screener()
    frame = _numeric_columns(
        frame,
        ["price", "change_pct", "change_1y_pct", "pe_ratio",
         "dividend_yield", "volume_avg_30d"],
    )

    # sector is a float code; the names live in sectors(), keyed as a
    # zero-padded four-character string.
    codes = pd.to_numeric(frame["sector"], errors="coerce")
    frame["sector_code"] = [
        f"{int(v):04d}" if pd.notna(v) else None for v in codes
    ]
    names = sectors()[["sector_code", "sector_name"]]
    frame = frame.merge(names, on="sector_code", how="left")

    frame = frame.drop(columns=["sector"])

    dropped: list[str] = []
    if not keep_sparse and len(frame):
        for column in frame.columns:
            if column in ("symbol", "sector_name", "sector_code", "listed_in"):
                continue
            values = pd.to_numeric(frame[column], errors="coerce")
            if values.notna().sum() == 0 and frame[column].dtype.kind not in "ifb":
                continue  # a text column, not an empty numeric one
            useful = (values.notna() & (values != 0)).mean()
            if useful < min_useful:
                dropped.append(column)
        frame = frame.drop(columns=dropped)

    ordered = ["symbol", "sector_name", "sector_code", "listed_in", "price",
               "change_pct", "change_1y_pct", "pe_ratio", "dividend_yield",
               "volume_avg_30d"]
    keep = [c for c in ordered if c in frame.columns]
    frame = frame[keep + [c for c in frame.columns if c not in keep]]
    frame.attrs["dropped_sparse"] = dropped
    return frame


def index(name: str = "KSE100") -> pd.DataFrame:
    """
    Constituents of a PSX index, with the string columns coerced to numbers.

    KSE100, KSE30, KMI30, ALLSHR and the rest of the portal's index codes.
    """
    frame = psxdata.indices(name.strip().upper())
    return _numeric_columns(
        frame,
        ["ldcp", "current", "change", "change_pct", "idx_weight",
         "idx_point", "volume", "freefloat_m", "market_cap_m"],
    )


def sectors() -> pd.DataFrame:
    """Sector summary — advances, declines, turnover, market cap per sector."""
    return psxdata.sectors()


def symbols(index_name: str | None = None) -> list[str]:
    """
    Every PSX ticker, or just one index's.

    Note this is well over a thousand entries and includes non-equity
    instruments — TFCs, bonds and rights all sit in the same list.
    """
    return psxdata.tickers(index_name) if index_name else psxdata.tickers()


def movers(index_name: str = "KSE100", top: int = 10) -> dict[str, pd.DataFrame]:
    """
    Best and worst movers in an index, by percent change.

    Returns {"gainers": ..., "losers": ...}. Rows with no usable change_pct
    are excluded rather than sorted to one end, where they would crowd out
    real movers.
    """
    frame = index(index_name)
    frame = frame[frame["change_pct"].notna()]
    columns = [c for c in ["symbol", "name", "current", "change",
                           "change_pct", "volume"] if c in frame.columns]
    ranked = frame.sort_values("change_pct", ascending=False)
    return {
        "gainers": ranked.head(top)[columns].reset_index(drop=True),
        "losers": ranked.tail(top)[columns]
                        .sort_values("change_pct")
                        .reset_index(drop=True),
    }


def _as_date(value) -> _dt.date | None:
    if value is None or isinstance(value, _dt.date):
        return value
    return _dt.date.fromisoformat(str(value))
