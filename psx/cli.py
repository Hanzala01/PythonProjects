#!/usr/bin/env python3
"""
psx.cli — read the Pakistan Stock Exchange from a terminal.

    python -m psx.cli quote OGDC LUCK HBL
    python -m psx.cli history OGDC --days 90
    python -m psx.cli history OGDC --days 365 --csv ogdc.csv
    python -m psx.cli movers --index KSE100 --top 5
    python -m psx.cli index KMI30
    python -m psx.cli sectors

Every command takes --csv to write the frame out instead of only printing a
truncated view of it.
"""

from __future__ import annotations

import argparse
import sys

import pandas as pd

from . import data


def _show(frame: pd.DataFrame, csv: str | None, title: str) -> None:
    if frame is None or frame.empty:
        print(f"{title}: nothing returned")
        return

    print(f"\n{title}  ({len(frame)} rows)")
    print("-" * min(len(title) + 20, 78))
    with pd.option_context("display.width", 200,
                           "display.max_columns", 40,
                           "display.max_rows", 60):
        print(frame.to_string(index=False))

    if csv:
        frame.to_csv(csv, index=False)
        print(f"\nwritten to {csv}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="psx",
        description="Free PSX data — end-of-day, no key, no account.",
    )
    parser.add_argument("--csv", help="write the result to this CSV file")
    sub = parser.add_subparsers(dest="command", required=True)

    p_quote = sub.add_parser("quote", help="latest snapshot for symbols")
    p_quote.add_argument("symbols", nargs="+")

    p_hist = sub.add_parser("history", help="daily OHLCV, oldest first")
    p_hist.add_argument("symbol")
    p_hist.add_argument("--days", type=int, default=180)
    p_hist.add_argument("--start")
    p_hist.add_argument("--end")
    p_hist.add_argument("--drop-anomalies", action="store_true",
                        help="remove bars the library flags as suspect")

    p_index = sub.add_parser("index", help="constituents of an index")
    p_index.add_argument("name", nargs="?", default="KSE100")

    p_movers = sub.add_parser("movers", help="best and worst in an index")
    p_movers.add_argument("--index", default="KSE100")
    p_movers.add_argument("--top", type=int, default=10)

    sub.add_parser("sectors", help="sector summary")

    p_symbols = sub.add_parser("symbols", help="list tickers")
    p_symbols.add_argument("--index", default=None)

    args = parser.parse_args(argv)

    try:
        if args.command == "quote":
            _show(data.quote(*args.symbols), args.csv,
                  f"Quote — {', '.join(s.upper() for s in args.symbols)}")

        elif args.command == "history":
            frame = data.history(
                args.symbol,
                start=args.start,
                end=args.end,
                days=args.days,
                drop_anomalies=args.drop_anomalies,
            )
            _show(frame, args.csv, f"{args.symbol.upper()} — daily OHLCV")

        elif args.command == "index":
            _show(data.index(args.name), args.csv,
                  f"{args.name.upper()} constituents")

        elif args.command == "movers":
            both = data.movers(args.index, top=args.top)
            _show(both["gainers"], None, f"{args.index.upper()} — top gainers")
            _show(both["losers"], args.csv, f"{args.index.upper()} — top losers")

        elif args.command == "sectors":
            _show(data.sectors(), args.csv, "PSX sector summary")

        elif args.command == "symbols":
            names = data.symbols(args.index)
            print(f"\n{len(names)} tickers"
                  + (f" in {args.index.upper()}" if args.index else "")
                  + "\n")
            print(", ".join(names))

    except Exception as exc:
        # The failure that actually happens is the portal changing shape or
        # being unreachable, and a bare traceback buries that in scraper
        # internals. Name it, then let --debug show the rest.
        print(f"psx: {type(exc).__name__}: {exc}", file=sys.stderr)
        print("psx: the portal may be down, rate-limiting, or its page "
              "layout may have changed.", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
