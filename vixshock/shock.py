"""Step 7a -- the market as of ONE date, and what the scenarios do to the VIX curve.

Everything here is read at a single pricing date -- the date in the book's filename --
and records the date each series was actually found at.  Nothing silently rolls back to
an earlier day: `Market.gaps()` lists every series whose date differs from the pricing
date, and price.py fails the run on any (gate curve_date).

Why exact: pricing Monday's marks off Friday's forwards does not fail on its own.  The
implied vol is inverted from the premium with the wrong forward, so the base price still
reproduces the mark to the cent -- the error only surfaces in the shocked price.

The book itself is priced in portfolio.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

import config
from .cm import build_cm
from .data_sources import load_cboe_index, load_spx
from .response import ResponseParams


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


@dataclass
class Market:
    asof: pd.Timestamp
    vix_curve: Curve           # CM futures + spot anchor: fallback forwards and the shocked-curve table
    vov_curve: Curve           # VVIX, flat: the last-resort fallback vol for a VIX option, VIX only
    spot_vix: float
    spx: float | None          # SPX close: the base of every SPX forward
    vx_settles: dict = field(default_factory=dict)  # listed VIX future expiry -> its settle ON asof
    dates: dict = field(default_factory=dict)   # series -> the date its value was actually read at

    def gaps(self, need_spx: bool) -> list[str]:
        """Series whose date is not the pricing date.  Empty = the curve_date gate passes."""
        keys = ["vix_futures_cm", "spot_vix"] + (["spx_close"] if need_spx else [])
        return [f"{k} is from {self.dates[k].date() if self.dates.get(k) is not None else 'nowhere'}"
                for k in keys if self.dates.get(k) != self.asof]


def _last_on_or_before(s: pd.Series | pd.DataFrame, asof):
    part = s.loc[:asof]
    return (part.iloc[-1], part.index[-1]) if len(part) else (None, None)


def current_futures() -> tuple[pd.DataFrame, pd.DataFrame]:
    """(every listed VIX future's settle by day, the constant-maturity curve), rebuilt from
    data/raw + data/daily_inputs in memory -- i.e. including whatever the daily refresh pulled.

    Not data/processed/cm_vix.csv: that file is rewritten only by run.py, which also refits,
    so a new day's settles would never reach a pricing run.  Rebuilding takes ~1 second,
    writes nothing, and matches the processed file to 1e-14 on every shared date."""
    import logging
    from . import ingest
    # ingest re-reports history's data-quality notes (2004 expiry offsets, two 2008 placeholder
    # settles) on every read.  They belong to the calibration report, not every pricing run.
    quiet = logging.getLogger(ingest.__name__)
    level = quiet.level
    quiet.setLevel(logging.ERROR)
    try:
        long, _ = ingest.run(save=False)
    finally:
        quiet.setLevel(level)
    wide, _ = build_cm(long, load_cboe_index("vix"))
    return long, wide


def load_market(asof=None) -> Market:
    """The market as of `asof` (default: the last curve date available).

    Reads the last value on or before `asof` for each series and RECORDS its date, so the
    caller can see -- and gate on -- any series that is not from the pricing date itself."""
    long, cm = current_futures()
    asof = pd.Timestamp(asof) if asof is not None else cm.index.max()
    row, cm_date = _last_on_or_before(cm, asof)
    today = long[long["date"] == asof]          # exact date only: no settle is carried forward
    vx_settles = dict(zip(pd.to_datetime(today["contract_expiry"]), today["settle"].astype(float)))
    if row is None:
        raise ValueError(f"no VIX futures curve on or before {asof.date()}")
    spot, spot_date = _last_on_or_before(load_cboe_index("vix"), asof)
    try:
        spx, spx_date = _last_on_or_before(load_spx(), asof)
    except FileNotFoundError:
        spx, spx_date = None, None
    # Last-resort vol for a VIX option with no premium (book_vols fails the run whenever it is
    # used): VVIX on the pricing date, flat across tenors.  Not data/processed/vov_levels.csv --
    # that is built by run.py, absent on a fresh clone, and was only as fresh as the last calibration.
    vvix, vvix_date = _last_on_or_before(load_cboe_index("vvix"), asof)
    return Market(
        asof=asof,
        vix_curve=Curve({T: float(row[f"cm_{T}"]) for T in config.TENORS}, spot=float(spot)),
        vov_curve=Curve({30: float(vvix) / 100.0}),
        spot_vix=float(spot),
        spx=None if spx is None else float(spx),
        vx_settles=vx_settles,
        dates={"vix_futures_cm": cm_date, "spot_vix": spot_date, "spx_close": spx_date,
               "vvix (fallback vol only, not gated)": vvix_date},
    )


def shocked_curve_table(params: ResponseParams, vix_curve: Curve, shocks=config.SHOCKS,
                        tenors=config.TENORS) -> pd.DataFrame:
    """The CM curve under each scenario, with VIX_FLOOR applied (bindings shown in the report)."""
    rows = {"base": {f"T{T}": vix_curve(T) for T in tenors}}
    for s in shocks:
        rows[f"{s:+.0%}"] = {f"T{T}": max(vix_curve(T) + params.dvix(s, T), config.VIX_FLOOR) for T in tenors}
    return pd.DataFrame(rows).T.round(2)
