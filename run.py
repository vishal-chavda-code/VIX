"""Run the whole pipeline with diagnostics.

    python run.py                      # from cached raw data (downloads on first run)
    python run.py --refresh            # re-download CFE / SPX / VIX / VVIX / Bloomberg first
    python run.py --book my_book.csv   # reprice a real book (columns: expiry, strike, type, quantity[, forward, vol])
    python run.py --asof 2020-03-16    # evaluate the curves as of a historical date
    python run.py --days-forward 5     # shocks read at tenor - 5 days, options aged 5 days

Exit code 0 if every gate passed, 1 otherwise.  The full report is also
written to output/report_<timestamp>.txt.
"""
import argparse
import datetime as dt
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd

from vixshock import data_sources, diagnostics


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true", help="re-download all raw data")
    ap.add_argument("--book", type=str, default=None)
    ap.add_argument("--asof", type=str, default=None)
    ap.add_argument("--days-forward", type=int, default=0)
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

    if args.refresh:
        data_sources.download_everything(force=True)
        try:
            from vixshock import bloomberg
            bloomberg.download_vix_impvol(force=True)
        except Exception as e:  # no terminal on this machine: keep the cached file
            print(f"Bloomberg refresh skipped: {e}")
    elif not data_sources.VX_DIR.exists() or not any(data_sources.VX_DIR.glob("*.csv")):
        data_sources.download_everything()

    book = pd.read_csv(args.book) if args.book else None
    text, ok = diagnostics.full_report(book, args.asof, args.days_forward)
    print(text)
    out = Path(__file__).resolve().parent / "output" / f"report_{dt.datetime.now():%Y%m%d_%H%M}.txt"
    out.parent.mkdir(exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(f"\nreport written to {out}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
