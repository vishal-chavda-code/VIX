"""CALIBRATE the model, validate it, and price a book.  SLOW (minutes) -- run YEARLY.

    python run.py                          # recalibrate from the full history + validate
    python run.py --book input/book.csv    # ... and price a book with it
    python run.py --asof 2020-03-16        # evaluate as of a historical date
    python run.py --days-forward 5         # shocks read at tenor - 5 days, options aged 5 days

    python price.py --book input/book.csv  # THE DAILY RUN: price only, ~1 second, no refit
    python bootstrap_history.py            # ONE-TIME, machine with internet: load 2004-now history

This refits the response function and the vol-of-vol layer from 22 years of history,
re-runs the 14 stress episodes, checks every gate and rewrites
output/response_params.json and output/vov_params.json.

Recalibration is a REVIEWED EVENT, not a routine step.  Doing it as a side effect of
pricing means the risk number moves for reasons unrelated to the book, two runs on the
same book disagree, and there is no fixed object for a validator to sign off on.  Use
price.py for day-to-day work; see README section 0b.

Today's rows come from the public CBOE/Yahoo files if config.DAILY_REFRESH and the
network is there (no key, no account), else from data/daily_inputs/.  The run FAILS if
the curve is older than config.MAX_DATA_AGE_DAYS.

Writes output/report_<timestamp>.txt and a full, self-contained calibration folder at
output/runs/<run-id>_calibrate/ (report, parameters, plots, manifest).

Exit code 0 if every gate passed, 1 otherwise.
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

    import config
    if not data_sources.VX_DIR.exists() or not any(data_sources.VX_DIR.glob("*.csv")):
        sys.exit("no history in data/raw/ -- run bootstrap_history.py once on a machine with internet, "
                 "or copy the data/ folder from one that has")
    if config.DAILY_REFRESH:
        try:
            print("daily network refresh:", data_sources.refresh_daily())
        except Exception as e:
            print(f"daily network refresh unavailable ({type(e).__name__}: {e}) -- using data/daily_inputs/ "
                  f"and the data on disk; the data_fresh gate applies")

    started = dt.datetime.now()
    book = pd.read_csv(args.book) if args.book else None
    text, ok = diagnostics.full_report(book, args.asof, args.days_forward)
    print(text)

    root = Path(__file__).resolve().parent
    out = root / "output" / f"report_{started:%Y%m%d_%H%M}.txt"
    out.parent.mkdir(exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(f"\nreport written to {out}")

    # A calibration is a reviewed event: keep the whole thing together, with the
    # parameters it produced and the plots that justify them.
    import json
    import shutil
    run_id = f"{started:%Y%m%d_%H%M%S}_calibrate"
    rundir = root / "output" / "runs" / run_id
    rundir.mkdir(parents=True, exist_ok=True)
    (rundir / "report.txt").write_text(text, encoding="utf-8")
    for name in ("response_params.json", "vov_params.json", "shocked_curves.csv",
                 "book_repricing_detail.csv", "book_repricing_summary.csv",
                 "step2_cm_vs_spot.png", "step4_response_fit.png", "step6_vov_fit.png"):
        src = root / "output" / name
        if src.exists():
            shutil.copy2(src, rundir / name)
    if args.book:
        shutil.copy2(args.book, rundir / f"book_input{Path(args.book).suffix}")

    params = {}
    for key, fn in (("vix_response", "response_params.json"), ("vol_of_vol", "vov_params.json")):
        f = rundir / fn
        if f.exists():
            d = json.loads(f.read_text())
            params[key] = {k: d.get(k) for k in
                           ("beta_0", "k", "lam", "beta_up_0", "lam_up",
                            "fit_start", "fit_end", "n_obs", "method_down", "quantile")}
    (rundir / "manifest.json").write_text(json.dumps({
        "run_id": run_id,
        "run_type": "calibrate (refit from full history) + validate + price",
        "run_at": started.strftime("%Y-%m-%d %H:%M:%S"),
        "asof": args.asof or "latest",
        "book_file": args.book or "(built-in example book)",
        "all_gates_pass": ok,
        "config": {"tenors": list(config.TENORS), "shocks": list(config.SHOCKS),
                   "down_branch_fit": config.DOWN_BRANCH_FIT,
                   "envelope_quantile": config.ENVELOPE_QUANTILE,
                   "pool_max_horizon": config.POOL_MAX_HORIZON,
                   "fit_start": config.FIT_START, "vov_source": config.VOV_SOURCE,
                   "risk_free_rate": config.RISK_FREE_RATE},
        "fitted_parameters": params,
        "note": "This is the calibration of record until the next one. Read report.txt "
                "section 8 (VERDICT) and section 5 (stress validation) before accepting it.",
    }, indent=2, default=str), encoding="utf-8")
    (root / "output" / "runs" / "LATEST_CALIBRATION.txt").write_text(
        f"{run_id}\n{'PASS' if ok else 'FAIL'}\n", encoding="utf-8")
    print(f"calibration run folder: output/runs/{run_id}/")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
