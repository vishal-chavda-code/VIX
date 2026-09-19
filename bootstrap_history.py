"""ONE-TIME: download the 2004-now history into data/raw/.  Needs internet.

    python bootstrap_history.py            # skip files already on disk
    python bootstrap_history.py --force    # re-download everything

Run this once, on a machine with internet, then carry the whole `data/` folder to
wherever the model runs.  Neither `run.py` nor `price.py` calls it; they refuse to
start if `data/raw/vx/` is empty and point you here.

What it pulls -- all public, no key, no account, no email:

    VIX futures 2004-2013   cdn.cboe.com/resources/futures/archive/...
    VIX futures 2014-now    cdn.cboe.com/data/us/futures/market_statistics/...
    spot VIX                cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv
    VVIX                    cdn.cboe.com/api/global/us_indices/daily_prices/VVIX_History.csv
    SPX                     Yahoo ^GSPC via yfinance

SPX is the only non-CBOE source, and it is CALIBRATION-ONLY -- the daily pricing path
never reads it (see README section 0b).  If Yahoo is not acceptable as a source, drop
your own `date,close` CSV at data/raw/spx.csv and this step is skipped.

Takes several minutes: it fetches ~270 contract files.  Expect INFO lines for months
with no listed contract; those are normal for 2004-2006.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--force", action="store_true",
                    help="re-download files that are already present")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    from vixshock import data_sources as ds

    print(f"downloading history into {ds.RAW}")
    print("this takes several minutes (~270 contract files)\n")
    try:
        ds.download_everything(force=args.force)
    except Exception as e:
        sys.exit(f"\nbootstrap failed ({type(e).__name__}: {e})\n"
                 f"If this machine has no internet, copy the data/ folder from one that has.")

    n = len(list(ds.VX_DIR.glob("*.csv"))) if ds.VX_DIR.exists() else 0
    print(f"\nVIX futures contract files: {n}")
    for name in ("vix", "vvix"):
        p = ds.RAW / f"{name}.csv"
        print(f"  {name + '.csv':12} {'present' if p.exists() else 'MISSING'}")
    spx = ds.RAW / "spx.csv"
    print(f"  {'spx.csv':12} {'present' if spx.exists() else 'MISSING (drop a date,close CSV here)'}")

    if n == 0:
        sys.exit("\nno contract files downloaded -- check network access to cdn.cboe.com")

    print("\nNext: `python run.py` to calibrate and validate.")
    print("Then: `python price.py --book input/<your book>.csv` for daily pricing.")


if __name__ == "__main__":
    main()
