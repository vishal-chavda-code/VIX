"""Price a book against the FROZEN calibration.  Fast path -- no refitting.

    python price.py --book my_book.csv         # price a book, ~1 second
    python price.py                            # the example book
    python price.py --asof 2020-03-16          # as of a historical date
    python price.py --days-forward 5           # shocks read at tenor - 5 days

This is the daily entry point.  It loads the ten fitted numbers from
output/response_params.json and output/vov_params.json and applies them to
today's curve.  It never refits.

    run.py     calibration + full validation + pricing.  Slow (minutes).
               Run YEARLY, or on a trigger, as a reviewed event.
    price.py   pricing only, against whatever run.py last produced.  Fast.
               Run daily, or whenever a new book arrives.

Why the split: refitting as a side effect of pricing means the risk number moves
for reasons unrelated to the book, two runs on the same book disagree, nothing
records which calibration produced which number, and there is no fixed object for
a validator to sign off on.  See README section 0b.

Exit code 0 if every gate passed, 1 otherwise.

Gates enforced here:
    data_fresh          the curve is not older than config.MAX_DATA_AGE_DAYS
    calibration_fresh   the calibration is not older than config.MAX_CALIBRATION_AGE_DAYS
    book_vols           every position was priced off its own premium or vol
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd

ROOT = Path(__file__).resolve().parent


def calibration_stamp() -> dict:
    """Identify the calibration in force: when it was fitted, over what, from what data."""
    import config
    out = {}
    for name, fn in (("vix_response", "response_params.json"), ("vol_of_vol", "vov_params.json")):
        path = ROOT / "output" / fn
        if not path.exists():
            out[name] = None
            continue
        d = json.loads(path.read_text())
        out[name] = {
            "file": fn,
            "written": dt.datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
            "fit_window": f"{d.get('fit_start')} -> {d.get('fit_end')}",
            "n_obs": d.get("n_obs"),
            "method": d.get("method_down"),
            "quantile": d.get("quantile"),
            "params": {k: round(float(d[k]), 4) for k in
                       ("beta_0", "k", "lam", "beta_up_0", "lam_up") if k in d},
        }
    out["vov_source"] = config.VOV_SOURCE
    return out


def calibration_age_days() -> int | None:
    """Calendar days since the response parameters were written."""
    path = ROOT / "output" / "response_params.json"
    if not path.exists():
        return None
    written = dt.datetime.fromtimestamp(path.stat().st_mtime).date()
    return (dt.date.today() - written).days


def main():
    ap = argparse.ArgumentParser(description="Price a VIX option book against the frozen calibration.")
    ap.add_argument("--book", type=str, default=None, help="book CSV; omit for the example book")
    ap.add_argument("--asof", type=str, default=None)
    ap.add_argument("--days-forward", type=int, default=0)
    ap.add_argument("--refresh", action="store_true",
                    help="pull today's public CBOE/Yahoo rows first (no key needed); ignored if offline")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

    import config
    from vixshock import shock

    lines: list[str] = []
    gates: dict[str, bool] = {}

    def say(s=""):
        print(s)
        lines.append(s)

    say(f"SPX -> VIX book pricing (frozen calibration)   run {dt.datetime.now():%Y-%m-%d %H:%M}")
    say("=" * 100)

    # ---- the calibration in force
    stamp = calibration_stamp()
    if stamp["vix_response"] is None:
        sys.exit("no calibration found in output/ -- run `python run.py` once to produce it")
    say("\nCALIBRATION IN FORCE")
    for name in ("vix_response", "vol_of_vol"):
        s = stamp[name]
        if s is None:
            say(f"  {name:>14}: MISSING")
            continue
        say(f"  {name:>14}: fitted {s['fit_window']}  n={s['n_obs']:,}  "
            f"{s['method']} q={s['quantile']}")
        say(f"  {'':>14}  written {s['written']}   {s['params']}")
    say(f"  {'vov source':>14}: {stamp['vov_source']}")

    cal_age = calibration_age_days()
    limit = getattr(config, "MAX_CALIBRATION_AGE_DAYS", 400)
    gates["calibration_fresh"] = cal_age is not None and cal_age <= limit
    say(f"\n  calibration is {cal_age} days old   GATE calibration_fresh: "
        f"{'PASS' if gates['calibration_fresh'] else 'FAIL'}  (limit {limit} days -- recalibrate yearly)")

    # ---- market data
    if args.refresh and config.DAILY_REFRESH:
        from vixshock import data_sources
        try:
            say(f"\ndaily refresh: {data_sources.refresh_daily()}")
        except Exception as e:
            say(f"\ndaily refresh unavailable ({type(e).__name__}) -- using data on disk; the data_fresh gate applies")

    last, age = shock.data_age_days(pd.Timestamp(args.asof) if args.asof else None)
    gates["data_fresh"] = args.asof is not None or age <= config.MAX_DATA_AGE_DAYS
    say(f"\nMARKET DATA\n  latest curve date {last.date()}, {age} days old   GATE data_fresh: "
        f"{'PASS' if gates['data_fresh'] else 'FAIL'}  (limit {config.MAX_DATA_AGE_DAYS} days"
        f"{'; not applied for --asof runs' if args.asof else ''})")

    # ---- price
    book = pd.read_csv(args.book) if args.book else None
    asof, spot, curves, detail, summary = shock.run(book, args.asof, args.days_forward, save=True)

    say(f"\n  as of {asof.date()}   spot VIX {spot:.2f}   days forward {args.days_forward}")
    say("\nSHOCKED CM VIX CURVE")
    say(curves.to_string())

    first = detail["scenario"].iloc[0]
    base = detail[detail["scenario"] == first][
        ["expiry", "strike", "type", "qty", "tenor", "fwd", "vol", "vol_source", "base_price"]]
    say(f"\nBOOK ({'supplied' if book is not None else 'EXAMPLE -- supply the real book'})")
    say(base.round(3).to_string(index=False))

    n_fallback = int((base["vol_source"] == "ATM curve (fallback)").sum())
    gates["book_vols"] = n_fallback == 0
    say(f"\n  {len(base) - n_fallback} of {len(base)} positions priced off their own premium/vol   "
        f"GATE book_vols: {'PASS' if gates['book_vols'] else 'FAIL'}")
    if n_fallback:
        say(f"  !!! {n_fallback} position(s) fell back to the ATM curve. That marks far-out-of-the-money")
        say(f"      options near zero -- supply a premium (mid where available) for every position.")

    say("\nBOOK P&L BY SCENARIO  (vol_of_vol_effect = what holding implied vol fixed would miss)")
    say(summary.to_string())

    say("\n" + "=" * 100)
    say("VERDICT")
    for k, v in gates.items():
        say(f"  {k:>18}: {'PASS' if v else 'FAIL'}")
    ok = all(gates.values())
    say(f"\n  {'ALL GATES PASS' if ok else '*** ONE OR MORE GATES FAILED -- DO NOT USE ***'}")
    say("\n  Note: this run did NOT recalibrate. Validation of the calibration itself")
    say("  (stress episodes, fit quality, parameter ranges) lives in `python run.py`.")

    # ---- write everything for this run into one dated, self-describing folder
    started = dt.datetime.now()
    run_id = f"{started:%Y%m%d_%H%M%S}_price"
    rundir = ROOT / "output" / "runs" / run_id
    rundir.mkdir(parents=True, exist_ok=True)

    (rundir / "report.txt").write_text("\n".join(lines), encoding="utf-8")
    curves.to_csv(rundir / "shocked_curves.csv")
    detail.to_csv(rundir / "positions.csv", index=False)
    summary.to_csv(rundir / "pnl_by_scenario.csv")
    if args.book:
        import shutil
        shutil.copy2(args.book, rundir / f"book_input{Path(args.book).suffix}")

    manifest = {
        "run_id": run_id,
        "run_type": "price (frozen calibration, no refit)",
        "run_at": started.strftime("%Y-%m-%d %H:%M:%S"),
        "asof": str(asof.date()),
        "book_file": args.book or "(built-in example book)",
        "n_positions": int(len(base)),
        "positions_priced_off_market": int(len(base) - n_fallback),
        "days_forward": args.days_forward,
        "spot_vix": round(float(spot), 4),
        "market_data": {"latest_curve_date": str(last.date()), "age_days": age,
                        "max_age_days": config.MAX_DATA_AGE_DAYS},
        "calibration": stamp,
        "calibration_age_days": cal_age,
        "scenarios": list(config.SHOCKS),
        "tenors": list(config.TENORS),
        "risk_free_rate": config.RISK_FREE_RATE,
        "gates": gates,
        "all_gates_pass": ok,
    }
    (rundir / "manifest.json").write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")

    latest = ROOT / "output" / "runs" / "LATEST.txt"
    latest.write_text(f"{run_id}\n{'PASS' if ok else 'FAIL'}\n", encoding="utf-8")

    print(f"\nrun id: {run_id}")
    print(f"written to output/runs/{run_id}/")
    print("  report.txt  manifest.json  positions.csv  pnl_by_scenario.csv  shocked_curves.csv"
          + ("  book_input.csv" if args.book else ""))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
