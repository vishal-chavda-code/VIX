"""Price a book against the FROZEN calibration.  Fast path -- no refitting.

    python price.py --book input/book_2026-09-18.csv     # download the day's data, price the book
    python price.py --book ... --no-refresh              # price on the data already on disk
    python price.py --book ... --days-forward 5          # options aged 5 days, shocks read at tenor - 5

Each run first downloads the latest public settles from CBOE: every listed VIX future, spot
VIX, VVIX and the SPX close.  No key, no account.  If that fails it carries on with the data on
disk plus data/daily_inputs/.

The pricing date is the date in the book's FILENAME, never today: tenors, the VIX futures and
the SPX close are all read as of that date, and the run FAILS unless the market data has a
row for exactly that date.  Each VIX option's forward is that day's settle of the VIX future
expiring with it.

This is the daily entry point.  It loads the ten fitted numbers from the CALIBRATION OF
RECORD -- the committed folder output/runs/<run-id>_calibrate/ named by
output/runs/LATEST_CALIBRATION.txt -- and applies them to the book.  It never refits.
(output/response_params.json is run.py's gitignored working copy; pricing never reads it,
so every machine that pulls the repo prices with the same reviewed calibration.)

    run.py     calibration + full validation (+ a book if given).  Slow (minutes).
               Run YEARLY, or on a trigger, as a reviewed event.  Becomes the calibration
               of record only if every gate passes.
    price.py   pricing only, against the calibration of record.  Fast.
               Run daily, or whenever a new book arrives.

Why the split: refitting as a side effect of pricing means the risk number moves for
reasons unrelated to the book, two runs on the same book disagree, nothing records which
calibration produced which number, and there is no fixed object for a validator to sign off
on.  See README section 0b.

Exit code 0 if every gate passed, 1 otherwise.

Gates enforced here:
    curve_date          the VIX curve, spot VIX and (if the book holds SPX) the SPX close are
                        all from the book's date.  If not, nothing is priced.
    calibration_fresh   the calibration is not older than config.MAX_CALIBRATION_AGE_DAYS
    book_vols           every position was priced off its own premium (or a book vol)
    book_forwards       every VIX position was priced off its own VIX future, not the CM curve
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


def calibration_stamp(folder: Path, manifest: dict) -> dict:
    """Identify the calibration in force: which run, when it was fitted, over what data."""
    import config
    out = {"run_id": manifest["run_id"], "run_at": manifest["run_at"]}
    for name, fn in (("vix_response", "response_params.json"), ("vol_of_vol", "vov_params.json")):
        path = folder / fn
        if not path.exists():
            out[name] = None
            continue
        d = json.loads(path.read_text())
        out[name] = {
            "file": f"output/runs/{folder.name}/{fn}",
            "written": manifest["run_at"][:16],
            "fit_window": f"{d.get('fit_start')} -> {d.get('fit_end')}",
            "n_obs": d.get("n_obs"),
            "method": d.get("method_down"),
            "quantile": d.get("quantile"),
            # Calibrations written before 2026-09-26 did not record the up branch: it was least squares.
            "method_up": d.get("method_up", "lsq"),
            "quantile_up": d.get("quantile_up"),
            "params": {k: round(float(d[k]), 4) for k in
                       ("beta_0", "k", "lam", "beta_up_0", "lam_up") if k in d},
        }
    out["vov_source"] = config.VOV_SOURCE
    return out


def calibration_age_days(manifest: dict) -> int:
    """Calendar days since the calibration ran -- from its manifest, not a file timestamp
    (a git clone or copy resets those to the day it happened)."""
    ran = dt.datetime.strptime(manifest["run_at"], "%Y-%m-%d %H:%M:%S").date()
    return (dt.date.today() - ran).days


def main():
    ap = argparse.ArgumentParser(description="Price a VIX + SPX option book against the frozen calibration.")
    ap.add_argument("--book", type=str, required=True,
                    help="book file; its name must carry the pricing date, e.g. book_2026-09-18.csv")
    ap.add_argument("--days-forward", type=int, default=0)
    ap.add_argument("--no-refresh", action="store_true",
                    help="skip the download of the latest CBOE rows and price on the data on disk "
                         "plus data/daily_inputs/ (the download is the default; no key needed)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

    import config
    from vixshock import portfolio, shock
    from vixshock.response import load_calibration_of_record
    from vixshock.validate import BookError, asof_from_filename, read_book

    lines: list[str] = []
    gates: dict[str, bool] = {}

    def say(s=""):
        print(s)
        lines.append(s)

    def verdict_and_exit(code: int):
        say("\n" + "=" * 100)
        say("VERDICT")
        for k, v in gates.items():
            say(f"  {k:>18}: {'PASS' if v else 'FAIL'}")
        say("\n  *** ONE OR MORE GATES FAILED -- DO NOT USE ***")
        sys.exit(code)

    try:
        asof = asof_from_filename(args.book)
        book = read_book(args.book)
    except BookError as e:
        sys.exit(f"BOOK REJECTED: {e}")

    say(f"SPX -> VIX + SPX book pricing (frozen calibration)   run {dt.datetime.now():%Y-%m-%d %H:%M}")
    say("=" * 100)

    # ---- the calibration in force: the committed one named by output/runs/LATEST_CALIBRATION.txt
    try:
        params, vov_params, cal_folder, cal_manifest = load_calibration_of_record()
    except (FileNotFoundError, RuntimeError) as e:
        sys.exit(f"NO USABLE CALIBRATION: {e}")
    stamp = calibration_stamp(cal_folder, cal_manifest)
    say(f"\nCALIBRATION IN FORCE   {stamp['run_id']} (run {stamp['run_at']}), "
        f"named by output/runs/LATEST_CALIBRATION.txt")
    for name in ("vix_response", "vol_of_vol"):
        s = stamp[name]
        if s is None:
            say(f"  {name:>14}: MISSING")
            continue
        up = s["method_up"] + (f" q={s['quantile_up']}" if s["method_up"] == "envelope" else "")
        say(f"  {name:>14}: fitted {s['fit_window']}  n={s['n_obs']:,}  down {s['method']} q={s['quantile']}  up {up}")
        say(f"  {'':>14}  written {s['written']}   {s['params']}")
    say(f"  {'vov source':>14}: {stamp['vov_source']}")
    frozen_up = stamp["vix_response"]["method_up"]
    if frozen_up != config.UP_BRANCH_FIT:
        say(f"\n  !!! config.UP_BRANCH_FIT is {config.UP_BRANCH_FIT!r} but the frozen calibration's up branch was "
            f"fitted by {frozen_up!r}.")
        say(f"      Rally scenarios use the {frozen_up!r} up branch until the next calibration (run.py).")

    cal_age = calibration_age_days(cal_manifest)
    limit = config.MAX_CALIBRATION_AGE_DAYS
    gates["calibration_fresh"] = cal_age is not None and cal_age <= limit
    say(f"\n  calibration is {cal_age} days old   GATE calibration_fresh: "
        f"{'PASS' if gates['calibration_fresh'] else 'FAIL'}  (limit {limit} days -- recalibrate yearly)")

    # ---- market data, as of the book's date.  Pull the day's public settles first (CBOE futures,
    #      spot VIX, VVIX, SPX close).  If that fails the run carries on with the disk and
    #      data/daily_inputs/, and the curve_date gate decides whether that is good enough.
    if config.DAILY_REFRESH and not args.no_refresh:
        from vixshock import data_sources
        try:
            say(f"\ndaily refresh: {data_sources.refresh_daily()}")
        except Exception as e:
            say(f"\ndaily refresh UNAVAILABLE ({type(e).__name__}: {e}) -- using data on disk and "
                f"data/daily_inputs/; the curve_date gate below decides whether that is enough")
    else:
        say("\ndaily refresh skipped -- pricing on data on disk and data/daily_inputs/")

    market = shock.load_market(asof)
    product = {k.upper(): v["underlying"] for k, v in config.PRODUCTS.items()}
    holds_spx = "issuer_name" in book and (book["issuer_name"].fillna("").str.strip().str.upper()
                                           .str.replace(r"\s+", " ", regex=True).map(product) == "SPX").any()
    gaps = market.gaps(need_spx=holds_spx)
    gates["curve_date"] = not gaps
    say(f"\nMARKET DATA   pricing date {asof.date()} (from the book filename)")
    for k, d in market.dates.items():
        say(f"  {k:>36}: {d.date() if d is not None else 'none'}")
    say(f"  GATE curve_date: {'PASS' if not gaps else 'FAIL'}  (every series must be from {asof.date()})")
    book_age = (pd.Timestamp.today().normalize() - asof).days
    if book_age > 3:
        say(f"  note: the book is dated {book_age} days before today -- confirm this is the book you meant")
    if gaps:
        say("  !!! " + "; ".join(gaps))
        say("      Nothing is priced: marks from one date against forwards from another reproduce the base")
        say("      price exactly and put the whole error in the shocked number.  Load the market data for")
        say(f"      {asof.date()} (let the download run, or data/daily_inputs/) or price the book it belongs with.")
        verdict_and_exit(1)

    # ---- price
    try:
        detail = portfolio.price_portfolio(book, params, vov_params, market, days_forward=args.days_forward)
        # the same book again with the floors removed, only to measure floor_effect: its warnings
        # would repeat the first pass's, so they are silenced
        quiet = logging.getLogger(portfolio.__name__)
        level = quiet.level
        quiet.setLevel(logging.ERROR)
        try:
            unfloored = portfolio.price_portfolio(book, params, vov_params, market, days_forward=args.days_forward,
                                                  floors=portfolio.NO_FLOORS, warn=lambda m: None)
        finally:
            quiet.setLevel(level)
    except BookError as e:
        sys.exit(f"BOOK REJECTED: {e}")
    summary = portfolio.summarize(detail)
    floors = portfolio.floor_report(detail, unfloored)
    bindings = portfolio.floor_bindings(detail)
    curves = shock.shocked_curve_table(params, market.vix_curve)

    say(f"\n  spot VIX {market.spot_vix:.2f}   SPX {market.spx if market.spx is not None else 'n/a'}   "
        f"days forward {args.days_forward}")
    say("\nSHOCKED CM VIX CURVE  (VIX_FLOOR applied -- see FLOORS)")
    say(curves.to_string())

    base = detail[detail["scenario"] == detail["scenario"].iloc[0]].set_index("cusip")[
        ["underlying", "expiry", "type", "strike", "quantity", "tenor", "premium", "premium_source", "zero_bid",
         "forward", "forward_source", "vol", "vol_source", "base_price"]]
    say("\nBOOK  (keyed by cusip; every row of positions.csv traces back to one of these)")
    say(base.round(4).to_string())

    fell_back = base["vol_source"].isin(portfolio.VOL_FALLBACKS)
    n_vol_fb = int(fell_back.sum())
    gates["book_vols"] = n_vol_fb == 0
    say(f"\n  {len(base) - n_vol_fb} of {len(base)} positions priced off their own premium/vol   "
        f"GATE book_vols: {'PASS' if gates['book_vols'] else 'FAIL'}")
    if n_vol_fb:
        say(f"  !!! {n_vol_fb} position(s) have no vol from their premium (blank, below intrinsic, or")
        say(f"      unreachable) and fell back -- VIX to the ATM curve, SPX to VOL_FLOOR_SPX: {list(base.index[fell_back])}")
        say(f"      Far-out-of-the-money options price near zero on a fallback -- supply a real premium.")
    n_fwd_fb = int((base["forward_source"] == portfolio.CURVE_FALLBACK).sum())
    gates["book_forwards"] = n_fwd_fb == 0
    say(f"  {int((base['underlying'] == 'VIX').sum()) - n_fwd_fb} of {int((base['underlying'] == 'VIX').sum())} "
        f"VIX positions priced off their own VIX future (the day's settle, or the book's forward)   "
        f"GATE book_forwards: {'PASS' if gates['book_forwards'] else 'FAIL'}")
    if n_fwd_fb:
        say(f"  !!! {n_fwd_fb} VIX position(s) have no listed future expiring on their expiry date (a weekly?)")
        say(f"      and no forward in the book, so fell back to the interpolated CM curve:")
        say(f"      {list(base.index[base['forward_source'] == portfolio.CURVE_FALLBACK])}")
        say(f"      The base price still matches the mark (the vol absorbs the error); the shocked price does not.")
        say(f"      Put that expiry's VIX future in the book's `forward` column.")
    expiring = sorted(set(book["cusip"].fillna("").astype(str).str.strip()) - set(base.index))
    if expiring:
        say(f"  note: {len(expiring)} position(s) expire ON the pricing date and are excluded "
            f"(settled, no scenario risk): {expiring}")
    n_zero_bid = int(base["zero_bid"].sum())
    if n_zero_bid:
        say(f"  note: {n_zero_bid} position(s) have a zero bid -- the least reliable marks: "
            f"{list(base.index[base['zero_bid']])}")
    n_beyond = int(detail.drop_duplicates("cusip")["beyond_calibrated_tenor"].sum())
    if n_beyond:
        say(f"  note: {n_beyond} position(s) are beyond the last calibrated tenor ({max(config.TENORS)}d); "
            f"their shock extrapolates the tenor fade")

    say("\nFLOORS  (a floor is a constant, not the model -- any non-zero floor_effect is P&L it set)")
    say(floors.to_string())
    if len(bindings):
        say(f"  every binding, per scenario and position: floors.csv ({len(bindings)} rows)")

    say("\nBOOK P&L BY SCENARIO  (currency: price * quantity * multiplier)")
    say("  vol_of_vol_effect = total vol shocked - total vol fixed: what holding implied vol fixed would miss")
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
    floors.to_csv(rundir / "floor_report.csv")
    bindings.to_csv(rundir / "floors.csv", index=False)
    import shutil
    shutil.copy2(args.book, rundir / f"book_input{Path(args.book).suffix}")

    import importlib.metadata as _md

    def _ver(pkg):
        try:
            return _md.version(pkg)
        except Exception:
            return None

    manifest = {
        "schema_version": 2,
        "run_id": run_id,
        "run_type": "price (frozen calibration, no refit)",
        "run_at": started.strftime("%Y-%m-%d %H:%M:%S"),
        "asof": str(asof.date()),
        "asof_source": "book filename",
        "book_file": args.book,
        "n_positions": int(len(base)),
        "excluded_expiring_on_pricing_date": expiring,
        "n_positions_by_underlying": {k: int(v) for k, v in base["underlying"].value_counts().items()},
        "positions_priced_off_market_vol": int(len(base) - n_vol_fb),
        "vix_positions_on_curve_forward": n_fwd_fb,
        "zero_bid_positions": n_zero_bid,
        "floor_bindings": int(len(bindings)),
        "days_forward": args.days_forward,
        "spot_vix": round(float(market.spot_vix), 4),
        "spx_close": market.spx,
        "market_data": {"dates": {k: (str(d.date()) if d is not None else None) for k, d in market.dates.items()}},
        "calibration": stamp,
        "calibration_age_days": cal_age,
        "config": {"up_branch_fit": config.UP_BRANCH_FIT, "up_envelope_quantile": config.UP_ENVELOPE_QUANTILE,
                   "vix_floor": config.VIX_FLOOR, "vol_floor_vix": config.VOL_FLOOR_VIX,
                   "vol_floor_spx": config.VOL_FLOOR_SPX, "spx_vol_scale": config.SPX_VOL_SCALE,
                   "spx_dividend_yield": config.SPX_DIVIDEND_YIELD},
        "scenarios": list(config.SHOCKS),
        "tenors": list(config.TENORS),
        "risk_free_rate": config.RISK_FREE_RATE,
        "gates": gates,
        "all_gates_pass": ok,
        "environment": {"python": sys.version.split()[0],
                        **{p: _ver(p) for p in ("pandas", "numpy", "scipy")}},
    }
    (rundir / "manifest.json").write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")

    latest = ROOT / "output" / "runs" / "LATEST.txt"
    latest.write_text(f"{run_id}\n{'PASS' if ok else 'FAIL'}\n", encoding="utf-8")

    print(f"\nrun id: {run_id}")
    print(f"written to output/runs/{run_id}/")
    print("  report.txt  manifest.json  positions.csv  pnl_by_scenario.csv  floor_report.csv  floors.csv"
          "  shocked_curves.csv  book_input.csv")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
