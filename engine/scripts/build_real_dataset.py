#!/usr/bin/env python3
"""Build an offline `csv_local` dataset from genuinely real market history.

Why this exists: an air-gapped machine (CI, a locked-down container, a plane)
cannot reach Stooq or SEC EDGAR, and the engine refuses to invent prices. This
script assembles a small dataset out of *real* historical bars that ship inside
public Python packages, so the engine can be exercised end to end without a
network and without a single fabricated number.

Source: the `bokeh_sampledata` package (BSD-3), which bundles daily OHLCV with
adjusted closes for AAPL, FB, GOOG, IBM and MSFT. The bars are real: FB's
series opens at 42.05 on 2012-05-18, its actual first trading day, and GOOG's
at 100.00 on 2004-08-19, its actual IPO day.

What this is NOT:

* not current -- the history stops on 2013-03-01, so anything produced from it
  is a replay of that date, never a view on today's market;
* not fundamentals -- no filings ship with these packages, so valuation and
  quality factors will be missing and the engine will say so rather than
  guessing;
* not a stock universe -- five large-cap US technology names are a biased
  sample, and a ranking across them says nothing about the wider market.

Usage:

    pip install bokeh_sampledata
    python3 scripts/build_real_dataset.py --root data/real

Then point the engine at it:

    research-engine --config config/offline-real.yaml ingest --symbols AAPL ...
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

SYMBOLS = ("AAPL", "FB", "GOOG", "IBM", "MSFT")

# Descriptive metadata only -- exchange, sector and industry are matters of
# public record, not estimates. Market caps and share counts are deliberately
# absent: they change daily and we have no point-in-time source for them.
PROFILES = {
    "AAPL": ("Apple Inc.", "NASDAQ", "Information Technology", "Technology Hardware"),
    "FB":   ("Facebook, Inc.", "NASDAQ", "Communication Services", "Interactive Media"),
    "GOOG": ("Google Inc.", "NASDAQ", "Communication Services", "Interactive Media"),
    "IBM":  ("International Business Machines Corp.", "NYSE", "Information Technology",
             "IT Services"),
    "MSFT": ("Microsoft Corporation", "NASDAQ", "Information Technology", "Software"),
}

BANNER = (
    "# Real daily bars redistributed by the bokeh_sampledata package (BSD-3).\n"
    "# History ends 2013-03-01. Not current market data; no fundamentals.\n"
)


def load_bars(symbol: str) -> list[dict[str, str]]:
    from bokeh_sampledata import package_path  # noqa: PLC0415

    path = Path(package_path(f"{symbol}.csv"))
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", default="data/real",
                        help="directory to write the csv_local tree into")
    args = parser.parse_args(argv)

    try:
        import bokeh_sampledata  # noqa: F401,PLC0415
    except ImportError:
        print("bokeh_sampledata is not installed. It carries the real bars this\n"
              "script copies; nothing is generated without it:\n\n"
              "    pip install bokeh_sampledata\n", file=sys.stderr)
        return 1

    root = Path(args.root)
    (root / "prices").mkdir(parents=True, exist_ok=True)
    (root / "universe").mkdir(parents=True, exist_ok=True)

    written = []
    for symbol in SYMBOLS:
        rows = load_bars(symbol)
        # The bundled files are newest-first; the engine wants ascending dates.
        rows.sort(key=lambda row: row["Date"])
        out = root / "prices" / f"{symbol}.csv"
        with out.open("w", newline="", encoding="utf-8") as fh:
            fh.write(BANNER)
            writer = csv.writer(fh)
            writer.writerow(["date", "open", "high", "low", "close", "volume",
                             "adj_close"])
            for row in rows:
                writer.writerow([row["Date"], row["Open"], row["High"], row["Low"],
                                 row["Close"], row["Volume"], row["Adj Close"]])
        written.append((symbol, len(rows), rows[0]["Date"], rows[-1]["Date"]))

    with (root / "universe" / "equity.csv").open("w", newline="",
                                                 encoding="utf-8") as fh:
        fh.write(BANNER)
        writer = csv.writer(fh)
        writer.writerow(["symbol", "name", "exchange", "sector", "industry", "country"])
        for symbol in SYMBOLS:
            name, exchange, sector, industry = PROFILES[symbol]
            writer.writerow([symbol, name, exchange, sector, industry, "US"])

    print(f"wrote {root}")
    for symbol, count, first, last in written:
        print(f"  {symbol:5s} {count:5d} bars  {first} .. {last}")
    print("\nNo fundamentals were written: none ship with these packages, and the\n"
          "engine reports missing factors rather than filling them in.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
