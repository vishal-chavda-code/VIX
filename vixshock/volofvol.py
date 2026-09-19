"""Step 6 -- vol-of-vol: how VIX-option implied vol moves with the SPX shock.

"When the market drops, VIX jumps and VIX options get more expensive too, so we
move both."  Same functional form and the same envelope fit as the VIX
response, applied to the change in ATM implied vol of VIX options:

    dSIGMA_T(r) = gamma_T * h(r; k_s),    gamma_T = gamma_0 * exp(-lam_s * T/30)

Data: fixed-tenor ATM implied vols (30/60/90/180d) from the frozen one-time
file in data/bloomberg_historical/ (see the README there -- NOT a dependency,
nothing calls Bloomberg), already constant-maturity so no stitching is needed.
The 120-day point comes from the continuous tenor function.  Units are vol
points (VVIX 90 -> 0.90).

Source selection (config.VOV_SOURCE):
  'historical'  the frozen file above (four tenors, tenor fade measured)
  'vvix'        the free CBOE VVIX file only (30d; tenor fade ASSUMED equal to
                the VIX response's lambda, and labelled as such in the report)
  'auto'        'historical' if the file exists, else 'vvix'
"""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd

import config
from .cm import PROCESSED
from .data_sources import ROOT, load_spx, load_cboe_index
from .join import build_pooled
from .response import ResponseParams, fit_response, fit_table

log = logging.getLogger(__name__)

VOV_MIN_PLAUSIBLE = 40.0     # VVIX has never printed below ~60; anything under 40 is a bad print
HISTORICAL_FILE = ROOT / "data" / "bloomberg_historical" / "vix_option_implied_vol.csv"
VOV_TENORS_HIST = (30, 60, 90, 180)
VOV_TENORS_VVIX = (30,)


def load_vov(source: str = config.VOV_SOURCE) -> tuple[pd.DataFrame, str, dict]:
    """Wide frame of implied-vol levels, columns cm_<T> (named so the join code
    can be reused), plus the source actually used and a cleaning report."""
    if source == "auto":
        source = "historical" if HISTORICAL_FILE.exists() else "vvix"
    if source == "historical":
        if not HISTORICAL_FILE.exists():
            raise FileNotFoundError(f"{HISTORICAL_FILE} missing; set VOV_SOURCE='vvix' or restore the file")
        raw = pd.read_csv(HISTORICAL_FILE, parse_dates=["date"]).set_index("date").sort_index()
        tenors = VOV_TENORS_HIST
    elif source == "vvix":
        raw = load_cboe_index("vvix").to_frame("vov_30")
        tenors = VOV_TENORS_VVIX
    else:
        raise ValueError(f"unknown VOV_SOURCE {source!r}")
    report = {"source": source, "tenors": tenors}
    clean = raw.where(raw >= VOV_MIN_PLAUSIBLE)
    report["implausible_nulled"] = {c: int((raw[c] < VOV_MIN_PLAUSIBLE).sum()) for c in raw.columns}
    report["first_date"] = {c: str(clean[c].first_valid_index().date()) for c in clean.columns}
    report["n_obs"] = {c: int(clean[c].notna().sum()) for c in clean.columns}
    wide = clean.rename(columns={f"vov_{T}": f"cm_{T}" for T in tenors})[[f"cm_{T}" for T in tenors]]
    wide.index.name = "date"     # the CBOE file names it DATE; downstream reads 'date'
    return wide, source, report


def build_vov_pool(wide: pd.DataFrame, tenors) -> pd.DataFrame:
    """Pooled 1..POOL_MAX_HORIZON windows of (SPX return, d implied vol per tenor).
    Rows with any tenor missing are dropped, so with the historical file the
    180d gap in 2009-2013 removes those years; the pool still spans 2006-08 and 2014-now."""
    spx = load_spx()
    pool = build_pooled(wide.dropna(how="all"), spx, tenors=tenors)
    return pool


def fit_vov(pool: pd.DataFrame, tenors) -> ResponseParams:
    params = fit_response(pool, tenors=tenors)
    if len(tenors) == 1:
        # VVIX only: no information on tenor damping -> borrow the VIX response's lambda (ASSUMPTION)
        from .response import load_params
        vp = load_params()
        params.lam, params.lam_up = vp.lam, vp.lam_up
        params.method_down += " (lam assumed = VIX response lam)"
    return params


def vov_grid(params: ResponseParams, tenors=config.TENORS) -> pd.DataFrame:
    return pd.DataFrame({f"T{T}": [params.dvix(s, T) for s in config.SHOCKS] for T in tenors},
                        index=[f"{s:+.0%}" for s in config.SHOCKS]).round(1)


def load_vov_params() -> ResponseParams:
    return ResponseParams.from_json(ROOT / "output" / "vov_params.json")


def run(save: bool = True):
    wide, source, report = load_vov()
    tenors = report["tenors"]
    pool = build_vov_pool(wide, tenors)
    params = fit_vov(pool, tenors)
    if save:
        (ROOT / "output").mkdir(exist_ok=True)
        wide.rename(columns={f"cm_{T}": f"vov_{T}" for T in tenors}).to_csv(PROCESSED / "vov_levels.csv")
        params.to_json(ROOT / "output" / "vov_params.json")
        from .response import plot_fit
        plot_fit(pool, params, ROOT / "output" / "step6_vov_fit.png", tenors=tenors)
    return params, report, pool


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    pd.set_option("display.width", 250)
    params, report, pool = run()
    print("vol-of-vol data:", report)
    print()
    print(params.summary().replace("response function", "vol-of-vol response"))
    print("\nfit quality per tenor / branch:")
    print(fit_table(params).T.to_string())
    print("\nmodel d(implied vol) in vol points at the scenario grid (120d from the tenor function):")
    print(vov_grid(params).to_string())
