# psx — free Pakistan Stock Exchange data

End-of-day PSX data with no API key, no account, and no cost. It wraps
[`psxdata`](https://github.com/mtauha/psxdata) (MIT), which scrapes PSX's own
public portal at [dps.psx.com.pk](https://dps.psx.com.pk/).

```bash
pip install -r psx/requirements.txt
```

## From the terminal

```bash
python -m psx.cli quote OGDC LUCK HBL
python -m psx.cli history OGDC --days 90
python -m psx.cli movers --index KSE100 --top 5
python -m psx.cli index KMI30
python -m psx.cli sectors
python -m psx.cli symbols --index KSE100
```

`--csv FILE` on any command writes the frame out instead of only printing it.

## From Python

```python
from psx import history, quote, movers, index, screener

history("OGDC", days=90)        # daily OHLCV, oldest row first
quote("OGDC", "LUCK", "HBL")    # latest portal snapshot, sector name attached
movers("KSE100", top=10)        # {"gainers": ..., "losers": ...}
index("KMI30")                  # constituents, numbers as numbers
screener()                      # all 745 listed symbols
```

Everything returns a pandas DataFrame.

## What this layer fixes

`psxdata` works. What it returns needs cleaning first, and each of these was
measured rather than assumed:

| Raw behaviour | What happens here |
|---|---|
| `stocks()` returns rows in **reverse** date order | sorted ascending — a `pct_change` over the raw frame is backwards |
| `indices()` gives `change_pct` as `'0.41%'`, `volume` as `'54,997'` | coerced to numbers, so sorting by volume stops comparing text |
| `screener()` gives sector as a bare float code (`820.0`) | joined to `sectors()` for the real name |
| `screener()` carries `market_cap` and `free_float` that look numeric but are not | dropped, names left in `frame.attrs["dropped_sparse"]` |
| `"Dates are not in chronological order"` logged on every history call | filtered — it contradicts the sorted frame it accompanies |

On the two empty columns, the actual counts over 745 rows: `market_cap` had 76
non-null values and **every one was 0.0**; `free_float` had 85 non-null and
**exactly one** that was not zero. They are dropped by ratio, not by name, so
if PSX starts publishing them they return on their own — or pass
`screener(keep_sparse=True)` to see them regardless.

## What this cannot do

Worth knowing before building on it:

- **No intraday bars.** No 1/5/15-minute history. PSX does not publish that
  archive, so no scraper can produce it. This is a limit of the source.
- **No order-book depth.** No bid/ask ladder.
- **Not real-time.** Quotes are the portal's snapshot and lag the live market.
- **It is scraping.** If PSX changes its page layout, this breaks. There is no
  contract here, and no support to call.

If any of those matter, the free route is the wrong tool and the options are
[CapitalStake's data API](https://www.capitalstake.com/data-solutions) (an
authorised PSX vendor — real-time, REST + WebSocket, sold without a brokerage
account, academic discount available) or a data licence direct from PSX
(`marketdatarequest@psx.com.pk`). Placing **orders** is a separate problem
again: that needs a SECP-regulated broker, and no PSX broker publishes a
retail trading API — the only documented route is
[StockIntel](https://stockintel.com/platforms/psx-trading-api), which requires
opening the brokerage account through them.

## Licensing of the data

PSX states that *"any dissemination, transmission, sale, and commercial use of
PSX market data feeds without acquiring respective rights/license is strictly
prohibited."* Personal research and your own bot are fine. Redistributing or
selling this data is not — that needs a licence.
