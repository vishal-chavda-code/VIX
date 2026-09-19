"""Step 7 -- apply the shock grid and reprice a book of VIX options.

Chain, per scenario r and per option with tenor T = days to expiry:

    forward_T          = the VIX future for the option's expiry (book mark, or the CM curve at T)
    shocked forward_T  = forward_T + dVIX_T(r)              (response function, step 4)
    shocked vol_T      = vol_T + dSIGMA_T(r)                (vol-of-vol, step 6)
    price              = Black-76(shocked forward, K, T, shocked vol)

Both "vol fixed" and "vol shocked" prices are produced so the effect of the
vol-of-vol layer is always visible.  Time to expiry is unchanged (instantaneous
shock) unless days_forward > 0, which also moves the tenor at which the
shocks are read.

Book file columns:  expiry (YYYY-MM-DD), strike, type (C/P), quantity
                    optional: forward          -- the future for that expiry; else the CM curve at that tenor
                              premium          -- PREFERRED.  The option's market price (mid of the
                                                  bid/ask where available).  The implied vol is backed
                                                  out of it with this model's own forward, day-count,
                                                  rate and Black-76, so the mark is exact by construction.
                              vol (decimal)    -- the option's implied vol, e.g. 1.27 for 127%.  Used
                                                  only when there is no premium, because an imported vol
                                                  carries its producer's conventions: one struck against
                                                  spot VIX rather than the future runs ~7 points high at
                                                  90 days, a ~59% error in the price.

Precedence is premium > vol > ATM curve.  If both a premium and a vol are given the
premium wins, and a gap of more than 1 vol point between them is logged -- that gap
usually means the supplied vol was struck under a different convention.

Positions with neither fall back to the ATM vol-of-vol curve, which badly understates
far-out-of-the-money options.  Every fallback is listed loudly in the output; supply a
premium for every position if you can.
"""
from __future__ import annotations

import datetime as dt
import logging

import numpy as np
import pandas as pd

import config
from .cm import load_cm
from .data_sources import ROOT, load_cboe_index
from .pricing import black76, implied_vol
from .response import ResponseParams, load_params

log = logging.getLogger(__name__)

VIX_FLOOR = 9.0           # shocked forward never below this (VIX futures have not traded lower)
VOL_FLOOR = 0.20          # shocked implied vol never below this


class Curve:
    """Continuous-in-tenor VIX futures curve from the CM points (+ spot at tenor 0)."""

    def __init__(self, levels: dict[int, float], spot: float | None = None):
        pts = dict(sorted(levels.items()))
        if spot is not None:
            pts = {0: spot, **pts}
        self.x = np.array(list(pts), dtype=float)
        self.y = np.array(list(pts.values()), dtype=float)

    def __call__(self, T):
        # linear between points, flat beyond the last
        return np.interp(np.asarray(T, dtype=float), self.x, self.y)


def data_age_days(asof: pd.Timestamp | None = None) -> tuple[pd.Timestamp, int]:
    """Last date in the CM curve and how many calendar days old it is."""
    cm = load_cm()
    last = cm.index.max()
    ref = pd.Timestamp(asof) if asof is not None else pd.Timestamp.today().normalize()
    return last, int((ref - last).days)


def latest_curves(asof: pd.Timestamp | None = None):
    """Base VIX curve and vol-of-vol curve as of a date (default: last available)."""
    cm = load_cm()
    asof = pd.Timestamp(asof) if asof is not None else cm.index.max()
    row = cm.loc[:asof].iloc[-1]
    asof = cm.loc[:asof].index[-1]
    spot = load_cboe_index("vix")
    spot_v = float(spot.loc[:asof].iloc[-1])
    vix_curve = Curve({T: float(row[f"cm_{T}"]) for T in config.TENORS}, spot=spot_v)
    vov = pd.read_csv(ROOT / "data" / "processed" / "vov_levels.csv", parse_dates=["date"]).set_index("date")
    vrow = vov.loc[:asof].ffill().iloc[-1]
    vov_curve = Curve({int(c.split("_")[1]): float(vrow[c]) / 100.0 for c in vov.columns if pd.notna(vrow[c])})
    return asof, vix_curve, vov_curve, spot_v


def shocked_curve_table(params: ResponseParams, vix_curve: Curve, shocks=config.SHOCKS,
                        tenors=config.TENORS) -> pd.DataFrame:
    rows = {"base": {f"T{T}": vix_curve(T) for T in tenors}}
    for s in shocks:
        rows[f"{s:+.0%}"] = {f"T{T}": max(vix_curve(T) + params.dvix(s, T), VIX_FLOOR) for T in tenors}
    return pd.DataFrame(rows).T.round(2)


def reprice_book(book: pd.DataFrame, params: ResponseParams, vov_params: ResponseParams,
                 vix_curve: Curve, vov_curve: Curve, asof: pd.Timestamp,
                 shocks=config.SHOCKS, days_forward: int = 0, r: float = config.RISK_FREE_RATE) -> pd.DataFrame:
    b = book.copy()
    b["expiry"] = pd.to_datetime(b["expiry"])
    b["dte"] = (b["expiry"] - asof).dt.days
    if (b["dte"] <= days_forward).any():
        raise ValueError("book contains options expiring on or before the evaluation date")
    b["tenor"] = b["dte"] - days_forward
    b["is_call"] = b["type"].str.upper().str.startswith("C")
    for col in ("forward", "vol", "premium"):
        if col not in b:
            b[col] = np.nan
    b["forward"] = b["forward"].fillna(pd.Series(vix_curve(b["tenor"]), index=b.index))
    Ty = b["tenor"] / 365.0
    # Vol source per position, in order of preference:
    #   1. premium  -- the observable.  Inverting it here guarantees the vol is consistent with
    #                  THIS model's forward, day-count, rate and Black-76.  An imported vol carries
    #                  whoever produced it's conventions; a vol struck off spot VIX rather than the
    #                  future is ~7 points high at 90d, which is a ~59% error in the price.
    #   2. vol      -- supplied directly.  Used when there is no premium.
    #   3. ATM curve -- flagged loudly; understates far-out-of-the-money options.
    b["vol_source"] = ""
    b["vol_from_premium"] = np.nan
    for i in b.index:
        prem, given = b.at[i, "premium"], b.at[i, "vol"]
        if pd.notna(prem):
            v = implied_vol(float(prem), float(b.at[i, "forward"]), float(b.at[i, "strike"]),
                            float(Ty[i]), r, bool(b.at[i, "is_call"]))
            b.at[i, "vol_from_premium"] = v
            if np.isfinite(v):
                b.at[i, "vol"], b.at[i, "vol_source"] = v, "from premium"
                continue
            log.warning("position %s: premium %.4f is below intrinsic; falling back", i, float(prem))
        if pd.notna(given):
            b.at[i, "vol"], b.at[i, "vol_source"] = float(given), "book vol"
        else:
            b.at[i, "vol"] = float(vov_curve(b.at[i, "tenor"]))
            b.at[i, "vol_source"] = "ATM curve (fallback)"

    # Both supplied?  The premium won.  A material gap means the supplied vol was struck under
    # different conventions (most often against spot VIX instead of the future) -- worth knowing.
    both = b.index[b["vol_from_premium"].notna() & b["premium"].notna()]
    for i in both:
        given = book["vol"].iloc[b.index.get_loc(i)] if "vol" in book else np.nan
        if pd.notna(given) and abs(float(given) - b.at[i, "vol_from_premium"]) > 0.01:
            log.warning("position %s: supplied vol %.1f%% vs %.1f%% implied by the premium (%.1f pts apart); "
                        "using the premium", i, float(given) * 100,
                        b.at[i, "vol_from_premium"] * 100,
                        abs(float(given) - b.at[i, "vol_from_premium"]) * 100)
    b = b.drop(columns=["vol_from_premium"])
    fallback = b.index[b["vol_source"] == "ATM curve (fallback)"]
    if len(fallback) and (b["vol_source"] != "ATM curve (fallback)").any():
        log.warning("book mixes market-derived vols with ATM fallbacks on %d position(s): %s",
                    len(fallback), list(fallback))
    b["base_price"] = black76(b["forward"], b["strike"], Ty, b["vol"], r, b["is_call"])
    out = []
    for s in shocks:
        dv = params.dvix(s, b["tenor"].to_numpy())
        ds = vov_params.dvix(s, b["tenor"].to_numpy()) / 100.0
        F1 = np.maximum(b["forward"] + dv, VIX_FLOOR)
        v1 = np.maximum(b["vol"] + ds, VOL_FLOOR)
        p_fixed = black76(F1, b["strike"], Ty, b["vol"], r, b["is_call"])
        p_full = black76(F1, b["strike"], Ty, v1, r, b["is_call"])
        for i in b.index:
            out.append({"scenario": f"{s:+.0%}", "spx_ret": s, "idx": i, "expiry": b.at[i, "expiry"].date(),
                        "strike": b.at[i, "strike"], "type": "C" if b.at[i, "is_call"] else "P",
                        "qty": b.at[i, "quantity"], "tenor": b.at[i, "tenor"],
                        "fwd": b.at[i, "forward"], "fwd_shocked": float(F1[i]),
                        "vol": b.at[i, "vol"], "vol_source": b.at[i, "vol_source"], "vol_shocked": float(v1[i]),
                        "base_price": float(b.at[i, "base_price"]),
                        "price_vol_fixed": float(p_fixed[i]), "price_vol_shocked": float(p_full[i]),
                        "pnl_vol_fixed": float((p_fixed[i] - b.at[i, "base_price"]) * b.at[i, "quantity"]),
                        "pnl_vol_shocked": float((p_full[i] - b.at[i, "base_price"]) * b.at[i, "quantity"])})
    return pd.DataFrame(out)


def book_summary(detail: pd.DataFrame) -> pd.DataFrame:
    """Book P&L per scenario, with the vol-of-vol contribution isolated."""
    g = detail.groupby("scenario", sort=False)[["pnl_vol_fixed", "pnl_vol_shocked"]].sum()
    g["vol_of_vol_effect"] = g["pnl_vol_shocked"] - g["pnl_vol_fixed"]
    g["spx_ret"] = detail.groupby("scenario", sort=False)["spx_ret"].first()
    return g.sort_values("spx_ret").drop(columns="spx_ret").round(2)


def example_book(asof: pd.Timestamp) -> pd.DataFrame:
    """A small illustrative book: the real one comes from the desk."""
    from .data_sources import vix_expiry_rule
    y, m = asof.year, asof.month
    exps = []
    for k in range(1, 6):
        mm = (m + k - 1) % 12 + 1
        yy = y + (m + k - 1) // 12
        exps.append(pd.Timestamp(vix_expiry_rule(yy, mm)))
    exps = [e for e in exps if (e - asof).days > 5][:4]
    rows = [
        {"expiry": exps[0], "strike": 20, "type": "C", "quantity": 100},
        {"expiry": exps[0], "strike": 30, "type": "C", "quantity": -200},
        {"expiry": exps[1], "strike": 25, "type": "C", "quantity": 100},
        {"expiry": exps[1], "strike": 16, "type": "P", "quantity": -100},
        {"expiry": exps[2], "strike": 22, "type": "C", "quantity": -100},
        {"expiry": exps[3], "strike": 30, "type": "C", "quantity": 50},
    ]
    return pd.DataFrame(rows)


def run(book: pd.DataFrame | None = None, asof=None, days_forward: int = 0, save: bool = True):
    from .volofvol import load_vov_params
    params, vov_params = load_params(), load_vov_params()
    asof, vix_curve, vov_curve, spot = latest_curves(asof)
    book = book if book is not None else example_book(asof)
    curves = shocked_curve_table(params, vix_curve)
    detail = reprice_book(book, params, vov_params, vix_curve, vov_curve, asof, days_forward=days_forward)
    summary = book_summary(detail)
    if save:
        curves.to_csv(ROOT / "output" / "shocked_curves.csv")
        detail.to_csv(ROOT / "output" / "book_repricing_detail.csv", index=False)
        summary.to_csv(ROOT / "output" / "book_repricing_summary.csv")
    return asof, spot, curves, detail, summary


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    pd.set_option("display.width", 250)
    book = pd.read_csv(sys.argv[1]) if len(sys.argv) > 1 else None
    asof, spot, curves, detail, summary = run(book)
    print(f"as of {asof.date()}   spot VIX {spot:.2f}")
    print("\nshocked CM VIX curve:")
    print(curves.to_string())
    print("\nbook (example unless a CSV was given):")
    base = detail[detail["scenario"] == detail["scenario"].iloc[0]][["expiry", "strike", "type", "qty", "tenor", "fwd", "vol", "base_price"]]
    print(base.round(3).to_string(index=False))
    print("\nbook P&L by scenario (vol fixed vs vol shocked; the difference is what holding vol fixed would miss):")
    print(summary.to_string())
