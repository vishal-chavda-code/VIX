"""Step 7b -- price a book of VIX and SPX options under the SPX scenario grid.

This is the whole pricing layer: book in, market in, the ten frozen numbers in, P&L out.

Per position, per scenario r (an SPX return), with T = days to expiry:

                    VIX option                              SPX option
  forward F         that expiry's VIX future settle         SPX close * exp((rate - div) * T)
  shocked F         max(F + dVIX_T(r), VIX_FLOOR)           F * (1 + r)
  vol               implied from the book premium, with this model's forward, rate and Black-76
  vol shock         dSIGMA_T(r)  (vol-of-vol layer)         SPX_VOL_SCALE * average dVIX over
                                                            tenors 0..T-30 / 100 (spx_vol_shock)
  shocked vol       max(vol + shock, VOL_FLOOR_VIX)         max(vol + shock, VOL_FLOOR_SPX)
  price             Black-76(shocked F, K, T, vol)  -> "vol fixed"
                    Black-76(shocked F, K, T, shocked vol) -> "vol shocked"
  P&L               (price - base price) * quantity * multiplier, in currency

The SPX leg needs no response function: the scenario IS the SPX move.  Its vol shock reuses
the VIX response, because VIX is 30-day SPX implied vol -- see README on the assumptions.

The vol shock is a parallel shift of each position's own implied vol.  The book carries one
vol per position, not a surface, so the skew cannot move with moneyness: this is
sticky-strike plus an ATM shift.  For long puts in a rally that is the conservative side of
sticky-moneyness (the put drifts further out, where the skew would have given it more vol).

Forward, per position:
  VIX   the settle, on the pricing date, of the VIX future expiring the same day as the
        option -- the contract it settles to.  A `forward` in the book overrides it (logged
        if they differ).  With neither -- a weekly, whose future is not in the data -- the CM
        curve at that tenor: flagged, and the book_forwards gate fails, because the error
        hides: the vol is inverted from the same wrong forward, so the base price still
        matches the mark and only the shock is off.
  SPX   the book's `forward` if given, else SPX close * carry (config.SPX_DIVIDEND_YIELD).

Vol, per position -- premium > vol > ATM curve:
  premium  the observable; inverting it here makes the base price reproduce the mark exactly
  vol      a legacy column, used only where there is no premium
  ATM      VIX only, flagged; understates far-out-of-the-money options (book_vols gate fails)
  SPX positions have no vol curve: without a usable premium they are priced at VOL_FLOOR_SPX,
  flagged, and book_vols fails -- the book still prices rather than being rejected.

Floors can set the answer in a scenario, so none of them acts silently: every binding is
flagged per position per scenario, and `floor_effect` measures the P&L each one contributes.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

import config
from .pricing import black76, implied_vol
from .response import ResponseParams
from .shock import Market
from .validate import BookError, validate_book

log = logging.getLogger(__name__)

FLOORS = {"vix": config.VIX_FLOOR, "VIX": config.VOL_FLOOR_VIX, "SPX": config.VOL_FLOOR_SPX}
# Floors effectively removed -- the forward and vol only kept positive -- to measure what the real ones contribute.
NO_FLOORS = {"vix": 0.01, "VIX": 0.001, "SPX": 0.001}
ATM_FALLBACK = "ATM curve (fallback)"
SPX_FALLBACK = "VOL_FLOOR_SPX (fallback)"
VOL_FALLBACKS = (ATM_FALLBACK, SPX_FALLBACK)    # either one fails the book_vols gate
CURVE_FALLBACK = "CM curve (fallback)"
VX_SETTLE = "VX settle"
SPX_MONEYNESS = (0.2, 5.0)      # strike / forward outside this is not an SPX option (XSP is ~0.1)


def _forwards(b: pd.DataFrame, market: Market, Ty: pd.Series, r: float) -> tuple[pd.Series, pd.Series]:
    fwd, src = b["forward"].copy(), pd.Series("book", index=b.index)
    is_vix = b["underlying"] == "VIX"
    # A VIX option settles to the VIX future expiring the same day: that contract's settle on
    # the pricing date IS its forward.  A lookup, not an interpolation.
    listed = b["expiry"].map(market.vx_settles)
    for i in b.index[is_vix & fwd.notna() & listed.notna()]:
        if abs(fwd[i] - listed[i]) > 0.01:
            log.warning("%s: book forward %.4f vs the %s VIX future's settle %.4f; using the book's",
                        b.at[i, "cusip"], fwd[i], b.at[i, "expiry"].date(), listed[i])
    use = is_vix & fwd.isna() & listed.notna()
    fwd[use], src[use] = listed[use], VX_SETTLE
    # No listed future on that date (a weekly, or the settles are missing): the CM curve is an
    # interpolation between other contracts -- flagged, and book_forwards fails the run.
    vix_blank = is_vix & fwd.isna()
    fwd[vix_blank] = market.vix_curve(b.loc[vix_blank, "tenor"])
    src[vix_blank] = CURVE_FALLBACK
    spx_blank = (b["underlying"] == "SPX") & fwd.isna()
    if spx_blank.any():
        if market.spx is None:
            raise BookError("the book holds SPX options but there is no SPX close for the pricing date -- "
                            "put it in data/daily_inputs/spx.csv")
        carry = r - config.SPX_DIVIDEND_YIELD
        fwd[spx_blank] = market.spx * np.exp(carry * Ty[spx_blank])
        src[spx_blank] = "SPX close + carry"
    return fwd, src


def _vols(b: pd.DataFrame, market: Market, Ty: pd.Series, r: float) -> tuple[pd.Series, pd.Series]:
    """premium > vol > ATM curve, per position.  See the module docstring."""
    vol, src = pd.Series(np.nan, index=b.index), pd.Series("", index=b.index)
    for i in b.index:
        prem, given = b.at[i, "premium"], b.at[i, "vol"]
        if pd.notna(prem):
            v = implied_vol(float(prem), float(b.at[i, "forward"]), float(b.at[i, "strike"]),
                            float(Ty[i]), r, b.at[i, "type"] == "C")
            if np.isfinite(v):
                vol[i], src[i] = v, "from premium"
                if pd.notna(given) and abs(float(given) - v) > 0.01:
                    log.warning("%s: supplied vol %.1f%% vs %.1f%% implied by the premium (%.1f pts apart); "
                                "using the premium", b.at[i, "cusip"], float(given) * 100, v * 100,
                                abs(float(given) - v) * 100)
                continue
            log.warning("%s: no vol reproduces premium %.4f against forward %.4f (below intrinsic, or above "
                        "what 500%% vol gives); falling back", b.at[i, "cusip"], float(prem),
                        float(b.at[i, "forward"]))
        if pd.notna(given):
            vol[i], src[i] = float(given), "book vol"
        elif b.at[i, "underlying"] == "VIX":
            vol[i], src[i] = float(market.vov_curve(b.at[i, "tenor"])), ATM_FALLBACK
        else:
            # SPX has no vol curve to fall back to.  Price it rather than reject the whole book --
            # a deep in-the-money put in a selloff can sit below intrinsic against the flat-carry
            # forward, and that is exactly when the book must still produce numbers.  Its value is
            # then mostly intrinsic, so the floor vol barely matters; book_vols fails regardless.
            vol[i], src[i] = config.VOL_FLOOR_SPX, SPX_FALLBACK
    return vol, src


def spx_vol_shock(params: ResponseParams, s: float, T) -> np.ndarray:
    """Change in a T-day SPX option's implied vol (decimal) for SPX return s.

    An option's implied vol covers its whole life, days 0..T.  The VIX future with t days left
    covers days t..t+30, so the futures spanning the option's life are tenors 0..T-30 (spot VIX
    alone for T <= 30).  The shock is the AVERAGE of the fitted response over those tenors --
    not the response at tenor T, which covers days T..T+30, after the option has expired, and
    which understated the move 1.3x at one month, 1.8x at three, 2.9x at six (switched 2026-09-26).

    The fitted fade is exponential, beta_0 * exp(-lam * t/30), so the average over 0..u*30 days
    is exact: beta_0 * (1 - exp(-lam*u)) / (lam*u).  Same fitted numbers, no new calibration.
    Tenor 0 extrapolates the futures fit to spot VIX, which in stress moves MORE than the fit
    says -- so the front end is, if anything, still understated."""
    u = np.maximum(np.asarray(T, dtype=float) - 30.0, 0.0) / 30.0
    lam = params.lam_up if s >= 0 else params.lam
    fade = np.where(u > 0, -np.expm1(-lam * u) / np.maximum(lam * u, 1e-12), 1.0)
    return config.SPX_VOL_SCALE * params.dvix(s, 0.0) * fade / 100.0


def price_portfolio(book: pd.DataFrame, params: ResponseParams, vov_params: ResponseParams, market: Market,
                    shocks=config.SHOCKS, days_forward: int = 0, r: float = config.RISK_FREE_RATE,
                    floors: dict = FLOORS, warn=None) -> pd.DataFrame:
    """One row per (position, scenario), keyed by cusip."""
    b = validate_book(book, market.asof, days_forward, warn=warn or (lambda m: log.warning("%s", m)))
    b = b.reset_index(drop=True)
    b["dte"] = (b["expiry"] - market.asof).dt.days
    b["tenor"] = b["dte"] - days_forward
    Ty = b["tenor"] / 365.0
    is_call = (b["type"] == "C").to_numpy()
    is_vix = (b["underlying"] == "VIX").to_numpy()
    b["forward"], b["forward_source"] = _forwards(b, market, Ty, r)
    # An SPX strike far from the index is a different product or a unit error -- XSP (a tenth of
    # SPX, multiplier also 100) labelled "S&P 500 INDEX" would otherwise price consistently, as
    # a strike-690 put on SPX at ~190% vol, and pass every other check.
    m = b["strike"] / b["forward"]
    odd = (b["underlying"] == "SPX") & ((m < SPX_MONEYNESS[0]) | (m > SPX_MONEYNESS[1]))
    if odd.any():
        raise BookError(f"SPX position(s) {list(b.loc[odd, 'cusip'])} have strikes "
                        f"{list(b.loc[odd, 'strike'])} against an SPX forward near {b.loc[odd, 'forward'].iloc[0]:.0f} -- "
                        f"outside {SPX_MONEYNESS[0]:.0%}-{SPX_MONEYNESS[1]:.0%} of it.  Wrong product (XSP is a tenth of "
                        f"SPX) or wrong units?")
    b["vol"], b["vol_source"] = _vols(b, market, Ty, r)
    b["base_price"] = black76(b["forward"], b["strike"], Ty, b["vol"], r, is_call)
    b["beyond_calibrated_tenor"] = b["tenor"] > max(config.TENORS)

    F, K, T, vol = (b[c].to_numpy(dtype=float) for c in ("forward", "strike", "tenor", "vol"))
    size = (b["quantity"] * b["multiplier"]).to_numpy(dtype=float)
    base = b["base_price"].to_numpy(dtype=float)
    # A floor stops the shock pushing vol below it -- it must never push vol UP.  A position whose
    # vol already sits below the floor (a deep in-the-money SPX put can imply 3%) is floored at
    # its own level instead, or a rally would raise its vol.
    vol_floor = np.minimum(np.where(is_vix, floors["VIX"], floors["SPX"]), vol)
    keep = ["cusip", "underlying", "expiry", "tenor", "type", "strike", "quantity", "multiplier",
            "premium", "premium_source", "zero_bid", "forward", "forward_source", "vol", "vol_source",
            "base_price", "beyond_calibrated_tenor"]
    out = []
    for s in shocks:
        dvix = params.dvix(s, T)
        F_raw = np.where(is_vix, F + dvix, F * (1.0 + s))
        F1 = np.where(is_vix, np.maximum(F_raw, floors["vix"]), F_raw)
        dvol = np.where(is_vix, vov_params.dvix(s, T) / 100.0, spx_vol_shock(params, s, T))
        v_raw = vol + dvol
        v1 = np.maximum(v_raw, vol_floor)
        p_fixed = black76(F1, K, T / 365.0, vol, r, is_call)
        p_full = black76(F1, K, T / 365.0, v1, r, is_call)
        d = b[keep].copy()
        d.insert(1, "scenario", f"{s:+.0%}")
        d.insert(2, "spx_ret", s)
        d["fwd_shocked_raw"], d["fwd_shocked"] = F_raw, F1
        d["vix_floor_bound"] = is_vix & (F_raw < floors["vix"])
        d["vol_shock"], d["vol_shocked_raw"], d["vol_shocked"] = dvol, v_raw, v1
        d["vol_floor_bound"] = v_raw < vol_floor
        d["price_vol_fixed"], d["price_vol_shocked"] = p_fixed, p_full
        d["pnl_vol_fixed"] = (p_fixed - base) * size
        d["pnl_vol_shocked"] = (p_full - base) * size
        out.append(d)
    return pd.concat(out, ignore_index=True)


def summarize(detail: pd.DataFrame) -> pd.DataFrame:
    """Currency P&L per scenario: VIX subtotal, SPX subtotal, portfolio total.

    vol_of_vol_effect = total vol shocked - total vol fixed: what holding every implied vol
    fixed would miss (VIX options: the vol-of-vol layer; SPX options: the SPX vol shock)."""
    g = detail.pivot_table(index=["spx_ret", "scenario"], columns="underlying",
                           values=["pnl_vol_fixed", "pnl_vol_shocked"], aggfunc="sum")
    out = pd.DataFrame(index=g.index)
    for u in ("VIX", "SPX"):
        for kind in ("vol_fixed", "vol_shocked"):
            col = (f"pnl_{kind}", u)
            out[f"{u}_{kind}"] = g[col] if col in g.columns else 0.0
    out["total_vol_fixed"] = out["VIX_vol_fixed"] + out["SPX_vol_fixed"]
    out["total_vol_shocked"] = out["VIX_vol_shocked"] + out["SPX_vol_shocked"]
    out["vol_of_vol_effect"] = out["total_vol_shocked"] - out["total_vol_fixed"]
    return out.sort_index().reset_index(level="spx_ret", drop=True).round(2)


def floor_bindings(detail: pd.DataFrame) -> pd.DataFrame:
    """Every (scenario, position) where a floor set the value: the log the report summarises."""
    rows = []
    for _, d in detail[detail["vix_floor_bound"]].iterrows():
        rows.append({"scenario": d["scenario"], "cusip": d["cusip"], "underlying": d["underlying"],
                     "floor": "VIX_FLOOR", "raw": d["fwd_shocked_raw"], "floored": d["fwd_shocked"],
                     "lifted_by": d["fwd_shocked"] - d["fwd_shocked_raw"]})
    for _, d in detail[detail["vol_floor_bound"]].iterrows():
        rows.append({"scenario": d["scenario"], "cusip": d["cusip"], "underlying": d["underlying"],
                     "floor": f"VOL_FLOOR_{d['underlying']}", "raw": d["vol_shocked_raw"],
                     "floored": d["vol_shocked"], "lifted_by": d["vol_shocked"] - d["vol_shocked_raw"]})
    cols = ["scenario", "cusip", "underlying", "floor", "raw", "floored", "lifted_by"]
    return pd.DataFrame(rows, columns=cols)


def floor_report(detail: pd.DataFrame, unfloored: pd.DataFrame) -> pd.DataFrame:
    """Per scenario: how many positions each floor bound, by how much, and the P&L it contributed.

    floor_effect = P&L with the floors - P&L with them removed (forward and vol only kept
    positive).  Non-zero means a hardcoded constant, not the model, set part of the answer."""
    b = floor_bindings(detail)
    rows = []
    for (ret, scen), d in detail.groupby(["spx_ret", "scenario"], sort=True):
        u = unfloored[unfloored["scenario"] == scen]
        row = {"scenario": scen}
        for name in ("VIX_FLOOR", "VOL_FLOOR_VIX", "VOL_FLOOR_SPX"):
            hit = b[(b["scenario"] == scen) & (b["floor"] == name)]
            row[f"{name} n"] = len(hit)
            row[f"{name} max lift"] = hit["lifted_by"].max() if len(hit) else 0.0
        row["floor_effect vol fixed"] = d["pnl_vol_fixed"].sum() - u["pnl_vol_fixed"].sum()
        row["floor_effect vol shocked"] = d["pnl_vol_shocked"].sum() - u["pnl_vol_shocked"].sum()
        rows.append(row)
    return pd.DataFrame(rows).set_index("scenario").round(2)
