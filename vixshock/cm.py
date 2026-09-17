"""Step 2 -- constant-maturity VIX futures series.

For each date and target tenor T (calendar days), take the two listed contracts
whose days-to-expiry straddle T and blend them linearly by distance:

    CM_T = w * settle_near + (1 - w) * settle_far,   w = (dte_far - T) / (dte_far - dte_near)

Edge cases, all flagged:
  * A contract with exactly T days: use it outright.
  * T shorter than the front contract (about 5 days a month for T=30): if
    CM_SPOT_ANCHOR, use spot VIX as the tenor-0 point -- a VIX future settles
    to VIX at expiry, so (0, spot) is a genuine point on the curve.
  * No pair at all: NaN, counted as an interpolation gap.
  * Either contract used fails the volume / open-interest filter: liquidity flag.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

import config
from .data_sources import ROOT

log = logging.getLogger(__name__)
PROCESSED = ROOT / "data" / "processed"


def _interp_one(cdte, csettle, cliq, cstale, T: int, spot: float | None):
    """Return (level, weight_near, dte_near, dte_far, liq_fail, stale, anchored, gap)."""
    below = np.where(cdte <= T)[0]
    above = np.where(cdte >= T)[0]
    if len(above) and cdte[above[0]] == T:
        i = above[0]
        return csettle[i], 1.0, cdte[i], cdte[i], bool(cliq[i]), bool(cstale[i]), False, False
    if len(below) and len(above):
        i, j = below[-1], above[0]
        w = (cdte[j] - T) / (cdte[j] - cdte[i])
        return (w * csettle[i] + (1 - w) * csettle[j], w, cdte[i], cdte[j],
                bool(cliq[i] or cliq[j]), bool(cstale[i] or cstale[j]), False, False)
    if len(above) and not len(below) and config.CM_SPOT_ANCHOR and spot is not None and np.isfinite(spot):
        j = above[0]
        w = (cdte[j] - T) / cdte[j]            # weight on the spot anchor at dte 0
        return w * spot + (1 - w) * csettle[j], w, 0, cdte[j], bool(cliq[j]), bool(cstale[j]), True, False
    return np.nan, np.nan, -1, -1, False, False, False, True


def build_cm(long: pd.DataFrame, spot: pd.Series | None = None,
             tenors=config.TENORS) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Returns (wide, detail).

    wide   : index date; columns cm_<T>, liq_<T> (bool), anchored_<T> (bool), gap_<T> (bool)
    detail : one row per date x tenor with the contracts and weight actually used
    """
    long = long.sort_values(["date", "days_to_expiry"])
    long = long.assign(liq_fail=(long["volume"] < config.MIN_VOLUME) | (long["open_interest"] < config.MIN_OI))
    spot = spot.reindex(long["date"].unique()) if spot is not None else None
    rows = []
    for date, g in long.groupby("date", sort=True):
        cdte = g["days_to_expiry"].to_numpy()
        cset = g["settle"].to_numpy()
        cliq = g["liq_fail"].to_numpy()
        cstale = g["stale"].to_numpy()
        s = float(spot.loc[date]) if spot is not None else None
        for T in tenors:
            lvl, w, dn, df_, liq, stale, anc, gap = _interp_one(cdte, cset, cliq, cstale, T, s)
            rows.append((date, T, lvl, w, dn, df_, liq, stale, anc, gap))
    detail = pd.DataFrame(rows, columns=["date", "tenor", "level", "w_near", "dte_near", "dte_far",
                                         "liq_fail", "stale", "anchored", "gap"])
    wide = detail.pivot(index="date", columns="tenor", values=["level", "liq_fail", "stale", "anchored", "gap"])
    rename = {"level": "cm", "liq_fail": "liq"}
    wide.columns = [f"{rename.get(a, a)}_{t}" for a, t in wide.columns]
    for c in wide.columns:
        if not c.startswith("cm_"):
            wide[c] = wide[c].astype(bool)
    return wide.sort_index(), detail


def cm_report(wide: pd.DataFrame, tenors=config.TENORS) -> pd.DataFrame:
    """Per-year counts of liquidity flags, spot-anchored days and gaps, per tenor."""
    yr = wide.index.year
    out = {}
    for T in tenors:
        out[f"liq_{T}"] = wide[f"liq_{T}"].groupby(yr).mean().round(3)
        out[f"stale_{T}"] = wide[f"stale_{T}"].groupby(yr).sum()
        out[f"anch_{T}"] = wide[f"anchored_{T}"].groupby(yr).sum()
        out[f"gap_{T}"] = wide[f"gap_{T}"].groupby(yr).sum()
    return pd.DataFrame(out)


def validate_against_spot(wide: pd.DataFrame, spot: pd.Series, out_png) -> dict:
    """The gate for this step: CM-30 must track spot VIX with a term premium in
    calm markets that flips negative (inversion) in stress."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    df = pd.DataFrame({"cm30": wide["cm_30"], "spot": spot}).dropna()
    prem = df["cm30"] - df["spot"]
    calm, stress = df["spot"] < 20, df["spot"] > 35
    stats = {
        "corr_level": round(df["cm30"].corr(df["spot"]), 4),
        "corr_daily_change": round(df["cm30"].diff().corr(df["spot"].diff()), 4),
        "term_premium_calm_median": round(prem[calm].median(), 2),
        "term_premium_stress_median": round(prem[stress].median(), 2),
        "pct_days_inverted_when_spot_gt_35": round((prem[stress] < 0).mean() * 100, 1),
    }
    fig, axes = plt.subplots(3, 1, figsize=(13, 11))
    for ax, (lo, hi, title) in zip(axes, [("2004", "2027", "full history"),
                                          ("2008-08", "2009-03", "2008"),
                                          ("2020-01", "2020-06", "COVID")]):
        sl = df.loc[lo:hi]
        ax.plot(sl.index, sl["spot"], lw=0.8, label="spot VIX")
        ax.plot(sl.index, sl["cm30"], lw=0.8, label="CM-30 futures")
        for T in (60, 90, 120):
            ax.plot(sl.index, wide.loc[sl.index, f"cm_{T}"], lw=0.5, alpha=0.6, label=f"CM-{T}")
        ax.set_title(f"CM VIX futures vs spot -- {title}")
        ax.legend(loc="upper left", fontsize=8)
    fig.tight_layout()
    fig.savefig(out_png, dpi=110)
    plt.close(fig)
    return stats


def load_cm() -> pd.DataFrame:
    return pd.read_csv(PROCESSED / "cm_vix.csv", parse_dates=["date"]).set_index("date")


def run(save: bool = True):
    from .data_sources import load_cboe_index
    long = pd.read_csv(PROCESSED / "vx_long.csv", parse_dates=["date", "contract_expiry"])
    spot = load_cboe_index("vix")
    wide, detail = build_cm(long, spot)
    if save:
        wide.to_csv(PROCESSED / "cm_vix.csv")
        detail.to_csv(PROCESSED / "cm_detail.csv", index=False)
    return wide, detail, spot


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    wide, detail, spot = run()
    (ROOT / "output").mkdir(exist_ok=True)
    stats = validate_against_spot(wide, spot, ROOT / "output" / "step2_cm_vs_spot.png")
    print("CM series:", wide.index.min().date(), "->", wide.index.max().date(), f"{len(wide)} days")
    print("\nvalidation vs spot VIX:")
    print("\n".join(f"{k:>36}: {v}" for k, v in stats.items()))
    print("\nper-year: share of days liquidity-flagged (liq), spot-anchored days (anch), gaps (gap):")
    print(cm_report(wide).to_string())
    print("\nsample:")
    print(wide.loc["2020-03-09":"2020-03-20", [c for c in wide.columns if c.startswith("cm_")]].round(2).to_string())
