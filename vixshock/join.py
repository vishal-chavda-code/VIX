"""Step 3 -- join SPX returns to the constant-maturity VIX series.

Core dataset, one row per date t (over a horizon of h trading days):
    spx_ret      = SPX_t / SPX_{t-h} - 1
    dvix_<T>     = CM_T(t) - CM_T(t-h)          in VIX points
    vix0_<T>     = CM_T(t-h)                    starting level, kept for diagnostics
    flag         = any liquidity / stale / anchor flag on either end date, any tenor

Dates are aligned on the intersection of the two calendars (CFE and NYSE
holidays differ on a handful of days); everything dropped is counted.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

import config
from .cm import PROCESSED, load_cm
from .data_sources import load_spx

log = logging.getLogger(__name__)


def build_dataset(cm: pd.DataFrame, spx: pd.Series, horizon: int = config.HORIZON_DAYS,
                  tenors=config.TENORS) -> tuple[pd.DataFrame, dict]:
    cm_dates, spx_dates = set(cm.index), set(spx.index)
    common = sorted(cm_dates & spx_dates)
    report = {
        "horizon_days": horizon,
        "cm_days": len(cm_dates), "spx_days_in_cm_range": int(((spx.index >= cm.index.min()) & (spx.index <= cm.index.max())).sum()),
        "joined_days": len(common),
        "cm_only": sorted(d.date() for d in cm_dates - spx_dates),
        "spx_only_in_range": sorted(d.date() for d in (spx_dates - cm_dates)
                                    if cm.index.min() <= d <= cm.index.max()),
    }
    cm = cm.loc[common]
    spx = spx.loc[common]

    out = pd.DataFrame(index=cm.index)
    out["spx_ret"] = spx / spx.shift(horizon) - 1
    out["spx_logret"] = np.log(spx / spx.shift(horizon))
    flag_cols = [c for c in cm.columns if c.split("_")[0] in ("liq", "stale", "anchored", "gap")]
    for T in tenors:
        out[f"dvix_{T}"] = cm[f"cm_{T}"] - cm[f"cm_{T}"].shift(horizon)
        out[f"vix0_{T}"] = cm[f"cm_{T}"].shift(horizon)
        tf = [c for c in flag_cols if c.endswith(f"_{T}")]
        out[f"flag_{T}"] = (cm[tf].any(axis=1) | cm[tf].shift(horizon, fill_value=False).any(axis=1))
    out["flag"] = out[[f"flag_{T}" for T in tenors]].any(axis=1)
    n0 = len(out)
    out = out.dropna(subset=["spx_ret"] + [f"dvix_{T}" for T in tenors])
    report["rows_dropped_nan"] = n0 - len(out)
    report["rows_final"] = len(out)
    report["rows_flagged"] = int(out["flag"].sum())
    return out, report


def build_pooled(cm: pd.DataFrame, spx: pd.Series, max_h: int = config.POOL_MAX_HORIZON,
                 tenors=config.TENORS) -> pd.DataFrame:
    """Windows of every length 1..max_h stacked, with a `horizon` column."""
    frames = []
    for h in range(1, max_h + 1):
        d, _ = build_dataset(cm, spx, h, tenors=tenors)
        frames.append(d.assign(horizon=h))
    return pd.concat(frames).sort_index()


def load_dataset(horizon: int = config.HORIZON_DAYS) -> pd.DataFrame:
    return pd.read_csv(PROCESSED / f"dataset_h{horizon}.csv", parse_dates=["date"]).set_index("date")


def load_pooled() -> pd.DataFrame:
    return pd.read_csv(PROCESSED / "dataset_pooled.csv", parse_dates=["date"]).set_index("date")


def run(horizon: int = config.HORIZON_DAYS, save: bool = True):
    cm = load_cm()
    spx = load_spx()
    ds, report = build_dataset(cm, spx, horizon)
    if save:
        ds.to_csv(PROCESSED / f"dataset_h{horizon}.csv")
        build_pooled(cm, spx).to_csv(PROCESSED / "dataset_pooled.csv")
    return ds, report


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ds, report = run()
    for k, v in report.items():
        print(f"{k:>22}: {v if not isinstance(v, list) else (len(v), v[:12])}")
    print("\ncorrelation of 1-day SPX return with dVIX by tenor:")
    print(ds[["spx_ret"] + [f"dvix_{T}" for T in config.TENORS]].corr().iloc[0, 1:].round(3).to_string())
    print("\n10 largest SPX down days:")
    cols = ["spx_ret"] + [f"dvix_{T}" for T in config.TENORS] + ["vix0_30", "flag"]
    print(ds.nsmallest(10, "spx_ret")[cols].round(3).to_string())
    print("\n10 largest SPX up days:")
    print(ds.nlargest(10, "spx_ret")[cols].round(3).to_string())
