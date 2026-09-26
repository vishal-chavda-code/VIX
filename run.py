"""CALIBRATE the model, validate it, and price a book.  SLOW (minutes) -- run YEARLY.

    python run.py                                   # recalibrate from the full history + validate
    python run.py --no-refresh                      # ... on exactly the data on disk (reproducible)
    python run.py --verify                          # CHECK the committed calibration reproduces here; changes nothing
    python run.py --book input/book_2026-09-18.csv  # ... and price a book, as of its filename date
    python run.py --asof 2020-03-16                 # shocked curve as of a historical date (no book)
    python run.py --days-forward 5                  # shocks read at tenor - 5 days, options aged 5 days

    python price.py --book input/book_<date>.csv    # THE DAILY RUN: price only, no refit
    python bootstrap_history.py                     # ONE-TIME, machine with internet: load 2004-now history

This refits the response function and the vol-of-vol layer from 22 years of history,
re-runs the 14 stress episodes, checks every gate, and writes the result to a
output/runs/<run-id>_calibrate/ folder.  If every gate passes, LATEST_CALIBRATION.txt is
pointed at that folder and it becomes the calibration of record that price.py uses; commit
both so other machines get it.  (output/response_params.json is only a working copy.)

Recalibration is a REVIEWED EVENT, not a routine step.  Doing it as a side effect of
pricing means the risk number moves for reasons unrelated to the book, two runs on the
same book disagree, and there is no fixed object for a validator to sign off on.  Use
price.py for day-to-day work; see README section 0b.

Today's rows come from the public CBOE files if config.DAILY_REFRESH and the
network is there (no key, no account), else from data/daily_inputs/.  --no-refresh skips
the network: with the data unchanged the refit is bit-identical to the last one, so a
change of estimator (e.g. config.UP_BRANCH_FIT) moves only the numbers it governs.

With --book, the pricing date is the date in the book's filename, and the run FAILS
(gate curve_date) unless the market data has a row for exactly that date.

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


def verify_against_record(root: Path, gates_ok: bool) -> int:
    """Compare the calibration this run just fitted (run.py's working copy in output/) with the
    calibration of record, field by field and exactly.  Returns the exit code: 0 = reproduced.

    What it proves: on this machine, with these library versions, the committed data and code
    produce exactly the committed ten numbers -- the calibration is the model, not an accident
    of the machine it was fitted on."""
    from vixshock.response import ResponseParams, load_calibration_of_record
    rec_p, rec_v, folder, manifest = load_calibration_of_record()
    fields = ("beta_0", "k", "lam", "beta_up_0", "lam_up", "fit_start", "fit_end", "n_obs",
              "method_down", "quantile", "method_up", "quantile_up")
    print("\n" + "=" * 100)
    print(f"VERIFY against the calibration of record {folder.name}")
    print("=" * 100)
    same, windows = True, True
    for label, rec, new_file in (("vix_response", rec_p, "response_params.json"),
                                 ("vol_of_vol", rec_v, "vov_params.json")):
        new = ResponseParams.from_json(root / "output" / new_file)
        print(f"\n  {label}")
        for f in fields:
            a, b = getattr(rec, f), getattr(new, f)
            ok = a == b
            same &= ok
            windows &= ok or f not in ("fit_start", "fit_end", "n_obs")
            print(f"    {f:>11}  record {a!s:>22}   refit {b!s:>22}   {'same' if ok else '*** DIFFERENT ***'}")
    print()
    if not gates_ok:
        print("  NOT VERIFIED: the refit failed its own gates -- see VERDICT above.")
    elif same:
        print("  VERIFIED: this machine reproduces the calibration of record exactly.")
    elif not windows:
        print("  NOT VERIFIED: the data on disk is not the calibration's data (fit window or row count")
        print("  differs), so the numbers are expected to move.  data/raw has probably been extended by")
        print("  daily downloads.  Restore the committed data -- `git stash` or `git checkout -- data/raw`")
        print("  (it re-downloads on the next price.py run) -- and run --verify again.")
    else:
        print("  NOT VERIFIED: same data, different numbers.  This machine does not reproduce the")
        print("  calibration -- compare its library versions with requirements.txt before using it.")
    print("  Nothing was changed: the calibration of record and LATEST_CALIBRATION.txt are untouched.")
    return 0 if (gates_ok and same) else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--book", type=str, default=None,
                    help="book file; its name must carry the pricing date, e.g. book_2026-09-18.csv")
    ap.add_argument("--asof", type=str, default=None, help="shocked-curve date when no book is given")
    ap.add_argument("--days-forward", type=int, default=0)
    ap.add_argument("--no-refresh", action="store_true", help="calibrate on exactly the data on disk")
    ap.add_argument("--verify", action="store_true",
                    help="re-run the whole calibration on the data on disk and check it reproduces the "
                         "calibration of record exactly.  Changes nothing: no download, no new "
                         "calibration folder, LATEST_CALIBRATION.txt untouched.  Exit 0 = reproduced")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    if args.verify:
        args.no_refresh = True     # the check is only meaningful on the calibration's own data

    import config
    from vixshock.validate import BookError, asof_from_filename, read_book
    if not data_sources.VX_DIR.exists() or not any(data_sources.VX_DIR.glob("*.csv")):
        sys.exit("no history in data/raw/ -- run bootstrap_history.py once on a machine with internet, "
                 "or copy the data/ folder from one that has")
    book, asof = None, args.asof
    if args.book:
        try:
            asof, book = asof_from_filename(args.book), read_book(args.book)
        except BookError as e:
            sys.exit(f"BOOK REJECTED: {e}")
        if args.asof and pd.Timestamp(args.asof) != asof:
            sys.exit(f"--asof {args.asof} disagrees with the book filename's date {asof.date()}; "
                     f"the book's date is the pricing date -- drop --asof")
    if config.DAILY_REFRESH and not args.no_refresh:
        try:
            print("daily network refresh:", data_sources.refresh_daily())
        except Exception as e:
            print(f"daily network refresh unavailable ({type(e).__name__}: {e}) -- using data/daily_inputs/ "
                  f"and the data on disk")

    started = dt.datetime.now()
    try:
        text, ok = diagnostics.full_report(book, asof, args.days_forward)
    except BookError as e:
        sys.exit(f"BOOK REJECTED: {e}")
    print(text)
    if args.verify:
        sys.exit(verify_against_record(Path(__file__).resolve().parent, ok))

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
    names = ["response_params.json", "vov_params.json", "shocked_curves.csv",
             "step2_cm_vs_spot.png", "step4_response_fit.png", "step6_vov_fit.png"]
    if args.book:     # otherwise output/ holds a PREVIOUS run's book files, which do not belong here
        names += ["book_repricing_detail.csv", "book_repricing_summary.csv"]
    for name in names:
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
                            "fit_start", "fit_end", "n_obs", "method_down", "quantile",
                            "method_up", "quantile_up")}
    (rundir / "manifest.json").write_text(json.dumps({
        "run_id": run_id,
        "run_type": "calibrate (refit from full history) + validate + price",
        "run_at": started.strftime("%Y-%m-%d %H:%M:%S"),
        "asof": str(pd.Timestamp(asof).date()) if asof is not None else "latest",
        "book_file": args.book or "(no book)",
        "network_refresh": bool(config.DAILY_REFRESH and not args.no_refresh),
        "all_gates_pass": ok,
        "config": {"tenors": list(config.TENORS), "shocks": list(config.SHOCKS),
                   "down_branch_fit": config.DOWN_BRANCH_FIT,
                   "envelope_quantile": config.ENVELOPE_QUANTILE,
                   "up_branch_fit": config.UP_BRANCH_FIT,
                   "up_envelope_quantile": config.UP_ENVELOPE_QUANTILE,
                   "pool_max_horizon": config.POOL_MAX_HORIZON,
                   "fit_start": config.FIT_START, "vov_source": config.VOV_SOURCE,
                   "risk_free_rate": config.RISK_FREE_RATE},
        "fitted_parameters": params,
        "note": "This is the calibration of record until the next one. Read report.txt "
                "section 8 (VERDICT) and section 5 (stress validation) before accepting it.",
    }, indent=2, default=str), encoding="utf-8")
    # price.py prices with the calibration named here.  Only a calibration that passed every gate
    # takes over; a failed one is kept for review but the previous calibration stays in force.
    pointer = root / "output" / "runs" / "LATEST_CALIBRATION.txt"
    if ok:
        pointer.write_text(f"{run_id}\nPASS\n", encoding="utf-8")
        print(f"calibration run folder: output/runs/{run_id}/  -- now the calibration of record "
              f"(commit this folder and LATEST_CALIBRATION.txt so every machine prices with it)")
    else:
        prev = pointer.read_text().split()[0] if pointer.exists() else "none"
        print(f"calibration run folder: output/runs/{run_id}/  -- FAILED its gates, NOT in force. "
              f"The calibration of record is still {prev}.")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
