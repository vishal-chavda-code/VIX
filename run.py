"""Run the whole pipeline with diagnostics.

    python run.py                      # DAILY.  History in data/raw/ + today's rows.  Today's rows come from
                                       # the CBOE/Yahoo public files if config.DAILY_REFRESH and the network is
                                       # there (no key, no account), else from data/daily_inputs/ (local CSVs).
                                       # Recalibrates, validates, prices the book.  FAILS if the curve is older
                                       # than config.MAX_DATA_AGE_DAYS.
    python bootstrap_history.py        # ONE-TIME, build machine only: downloads the 2004-now history.
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
    ap.add_argument("--book", type=str, default=None)
    ap.add_argument("--asof", type=str, default=None)
    ap.add_argument("--days-forward", type=int, default=0)
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

    if not data_sources.VX_DIR.exists() or not any(data_sources.VX_DIR.glob("*.csv")):
        sys.exit("no history in data/raw/ -- run bootstrap_history.py once on a machine with internet, "
                 "or copy the data/ folder from one that has")
    import config
    if config.DAILY_REFRESH:
        try:
            print("daily network refresh:", data_sources.refresh_daily())
        except Exception as e:
            print(f"daily network refresh unavailable ({type(e).__name__}: {e}) -- using data/daily_inputs/ "
                  f"and the data on disk; the data_fresh gate applies")

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
