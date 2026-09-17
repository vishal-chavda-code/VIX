"""Step 8 -- self-diagnostics.  Everything a run must tell you, on its own.

Nothing comes back from the work machine, so every run prints:
  * data coverage and what was dropped or flagged (liquidity, stale, anchored, gaps)
  * CM-30 vs spot validation with a PASS/FAIL
  * fitted parameters with the fit window, and a LOUD warning outside PARAM_RANGES
  * fit quality per tenor, per branch (up / down separately)
  * the stress-episode table with a PASS/FAIL
  * regime stability (post-2012 vs full) and envelope-vs-least-squares comparison
  * the vol-of-vol fit and the same checks
  * the shock grid and the book repricing with the vol-of-vol effect isolated
"""
from __future__ import annotations

import datetime as dt
import io
import logging
from contextlib import redirect_stdout

import pandas as pd

import config
from .data_sources import ROOT

log = logging.getLogger(__name__)
BANNER = "=" * 100


def check_ranges(params, ranges: dict, label: str) -> list[str]:
    problems = []
    for name, (lo, hi) in ranges.items():
        v = getattr(params, name)
        if not (lo <= v <= hi):
            problems.append(f"!!! {label}: {name} = {v:.3f} is OUTSIDE the sane range [{lo}, {hi}]")
    return problems


def section(title: str) -> str:
    return f"\n{BANNER}\n{title}\n{BANNER}"


def full_report(book: pd.DataFrame | None = None, asof=None, days_forward: int = 0) -> tuple[str, bool]:
    """Runs every step from the raw data and returns (report text, all gates passed)."""
    from . import ingest, cm, join, response, stress, volofvol, shock
    pd.set_option("display.width", 250)
    pd.set_option("display.max_columns", 40)
    buf = io.StringIO()
    gates: dict[str, bool] = {}
    warnings: list[str] = []
    with redirect_stdout(buf):
        print(f"SPX -> VIX shock model   run {dt.datetime.now():%Y-%m-%d %H:%M}")
        print(f"config: tenors {config.TENORS}  shocks {config.SHOCKS}  pool 1-{config.POOL_MAX_HORIZON}d  "
              f"down-branch fit {config.DOWN_BRANCH_FIT} q={config.ENVELOPE_QUANTILE}  fit start {config.FIT_START}  "
              f"exclude flagged {config.FIT_EXCLUDE_FLAGGED}  vol-of-vol source {config.VOV_SOURCE}")

        # ---------------- step 1
        print(section("1. INGEST"))
        long, rep = ingest.run()
        print("\n".join(f"  {k:>32}: {v}" for k, v in rep.items()))
        cpd = ingest.contracts_per_date(long)
        yr = cpd.groupby(cpd.index.year).agg(n_med=("n_contracts", "median"), n_min=("n_contracts", "min"),
                                             reach_min=("max_dte", "min"))
        print("\n  contracts listed per date by year (median, min) and minimum curve reach in days:")
        print(yr.T.to_string())

        # ---------------- step 2
        print(section("2. CONSTANT-MATURITY SERIES"))
        wide, detail, spot = cm.run()
        stats = cm.validate_against_spot(wide, spot, ROOT / "output" / "step2_cm_vs_spot.png")
        print("\n".join(f"  {k:>36}: {v}" for k, v in stats.items()))
        gates["cm_vs_spot"] = (stats["corr_level"] >= config.CM_MIN_CORR and
                               stats["pct_days_inverted_when_spot_gt_35"] >= config.CM_MIN_INVERSION_PCT)
        print(f"  GATE cm_vs_spot: {'PASS' if gates['cm_vs_spot'] else 'FAIL'}  "
              f"(corr >= {config.CM_MIN_CORR}, inversion >= {config.CM_MIN_INVERSION_PCT}%)")
        r = cm.cm_report(wide)
        print("\n  per-year share of days liquidity-flagged / stale-settle days / spot-anchored days / gaps, per tenor:")
        print(r.to_string())
        tot = {T: {"liq_days": int(wide[f"liq_{T}"].sum()), "stale_days": int(wide[f"stale_{T}"].sum()),
                   "anchored_days": int(wide[f"anchored_{T}"].sum()), "gap_days": int(wide[f"gap_{T}"].sum())}
               for T in config.TENORS}
        print("\n  totals:", tot)
        if any(v["gap_days"] for v in tot.values()):
            warnings.append("!!! interpolation gaps present: " + str({T: v["gap_days"] for T, v in tot.items()}))
        gaps = wide[[f"gap_{T}" for T in config.TENORS]].any(axis=1)
        if gaps.any():
            print("  gap dates:", [d.date() for d in wide.index[gaps]][:30])

        # ---------------- step 3
        print(section("3. SPX JOIN"))
        ds, rep = join.run()
        for k, v in rep.items():
            print(f"  {k:>22}: {v if not isinstance(v, list) else (len(v), v[:10])}")

        # ---------------- step 4
        print(section("4. RESPONSE FUNCTION"))
        params = response.run()
        print(params.summary())
        probs = check_ranges(params, config.PARAM_RANGES, "VIX response")
        warnings += probs
        gates["params_in_range"] = not probs
        print("\n".join(probs) if probs else "  all parameters inside PARAM_RANGES")
        print("\n  fit quality per tenor / branch (down = envelope, up = least squares):")
        print(response.fit_table(params).T.to_string())
        print("\n  model dVIX (points) at the scenario grid:")
        grid = pd.DataFrame({f"T{T}": [params.dvix(s, T) for s in config.SHOCKS] for T in config.TENORS},
                            index=[f"{s:+.0%}" for s in config.SHOCKS]).round(1)
        print(grid.T.to_string())
        print("\n  envelope vs least squares (section 3 of the spec -- why the average fit is not used):")
        print(response.lsq_comparison(params).to_string())
        print("\n  horizon comparison (fixed windows extrapolate; the pooled fit is the calibration):")
        print(response.horizon_comparison().to_string())
        print("\n  regime stability (known limitation 3):")
        pool = join.load_pooled()
        rows = []
        for name, a, b in [("2004-now (used)", config.FIT_START, None), ("2013-now", "2013-01-01", None),
                           ("2004-2012", config.FIT_START, "2012-12-31")]:
            p = response.fit_response(pool, fit_start=a, fit_end=b)
            rows.append({"window": name, "n": p.n_obs, "beta_0": p.beta_0, "k": p.k, "lam": p.lam,
                         "dVIX30(-10%)": p.dvix(-.10, 30), "dVIX30(-20%)": p.dvix(-.20, 30),
                         "dVIX120(-20%)": p.dvix(-.20, 120)})
        stab = pd.DataFrame(rows).set_index("window").round(2)
        print(stab.to_string())
        if stab.loc["2013-now", "beta_0"] > 1.15 * stab.loc["2004-now (used)", "beta_0"]:
            warnings.append(f"!!! post-2012 beta_0 ({stab.loc['2013-now', 'beta_0']:.0f}) is materially above the "
                            f"full-sample value ({stab.loc['2004-now (used)', 'beta_0']:.0f}); the recent regime "
                            f"responds harder than the calibration")

        # ---------------- step 5
        print(section("5. STRESS-EPISODE VALIDATION  (ratio = model / realised; < 1 = understated)"))
        t = stress.stress_table(params, wide)
        print(stress.format_stress(t))
        summ = stress.stress_summary(t)
        print("\n  summary:", summ)
        ok = all(v["understated"] <= config.STRESS_MAX_UNDERSTATED and v["min_ratio"] >= config.STRESS_MIN_RATIO
                 for v in summ.values())
        gates["stress"] = ok
        under = t[[f"ratio_{T}" for T in config.TENORS]].lt(1).any(axis=1)
        print(f"  understated episodes: {list(t.index[under])}")
        print(f"  GATE stress: {'PASS' if ok else 'FAIL'}  (<= {config.STRESS_MAX_UNDERSTATED} understated per tenor, "
              f"min ratio >= {config.STRESS_MIN_RATIO})")

        # ---------------- step 6
        print(section("6. VOL-OF-VOL"))
        vparams, vrep, vpool = volofvol.run()
        print("  data:", vrep)
        print(vparams.summary().replace("response function", "vol-of-vol response"))
        probs = check_ranges(vparams, config.PARAM_RANGES_VOV, "vol-of-vol")
        warnings += probs
        gates["vov_params_in_range"] = not probs
        print("\n".join(probs) if probs else "  all parameters inside PARAM_RANGES_VOV")
        print("\n  fit quality per tenor / branch:")
        print(response.fit_table(vparams).T.to_string())
        print("\n  model d(implied vol), vol points, at the scenario grid:")
        print(volofvol.vov_grid(vparams).T.to_string())

        # ---------------- step 7
        print(section("7. SHOCK GRID AND BOOK REPRICING"))
        asof_, spot_v, curves, det, summary = shock.run(book, asof, days_forward)
        print(f"  as of {asof_.date()}   spot VIX {spot_v:.2f}   days forward {days_forward}")
        print("\n  shocked CM VIX curve:")
        print(curves.to_string())
        base = det[det["scenario"] == det["scenario"].iloc[0]][["expiry", "strike", "type", "qty", "tenor", "fwd", "vol", "base_price"]]
        print(f"\n  book ({'supplied' if book is not None else 'EXAMPLE -- supply the real book'}):")
        print(base.round(3).to_string(index=False))
        print("\n  book P&L by scenario; vol_of_vol_effect is what holding implied vol fixed would have missed:")
        print(summary.to_string())

        # ---------------- verdict
        print(section("8. VERDICT"))
        for k, v in gates.items():
            print(f"  {k:>22}: {'PASS' if v else 'FAIL'}")
        for w in warnings:
            print(" ", w)
        all_ok = all(gates.values())
        print(f"\n  {'ALL GATES PASS' if all_ok else '*** ONE OR MORE GATES FAILED -- DO NOT USE ***'}"
              f"{'' if not warnings else '   (with warnings above)'}")
    return buf.getvalue(), all(gates.values())
