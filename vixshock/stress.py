"""Step 5 -- stress-episode validation.  This gates everything downstream.

For each episode: SPX return from the pre-stress start to the trough, the
realised change in each CM tenor over the same window, and what the model
predicts for that SPX return.  `ratio` = model / realised; below 1 means the
model UNDERSTATES what actually happened, which is the failure mode.

Windows longer than POOL_MAX_HORIZON days are outside the calibration pool
and are marked; the model is still applied (it is what would be used).
"""
from __future__ import annotations

import pandas as pd

import config
from .cm import load_cm
from .data_sources import load_spx, ROOT
from .response import ResponseParams

# (label, start = last close before the stress, end = SPX trough close)
EPISODES = [
    ("2008 Lehman -> Oct-10 low",     "2008-09-12", "2008-10-10"),
    ("2008 Lehman -> Oct-27 low",     "2008-09-12", "2008-10-27"),
    ("2008 Lehman -> Nov-20 low",     "2008-09-12", "2008-11-20"),
    ("2008 Oct 1-10 (fast leg)",      "2008-10-01", "2008-10-10"),
    ("2010 May flash-crash leg",      "2010-04-23", "2010-05-20"),
    ("2011 Aug US downgrade",         "2011-07-22", "2011-08-08"),
    ("2015 Aug China deval",          "2015-08-17", "2015-08-25"),
    ("2018 Feb Volmageddon (XIV day)","2018-01-26", "2018-02-05"),
    ("2018 Feb Volmageddon -> trough","2018-01-26", "2018-02-08"),
    ("2020 COVID -> Mar-16",          "2020-02-19", "2020-03-16"),
    ("2020 COVID -> Mar-23 low",      "2020-02-19", "2020-03-23"),
    ("2020 COVID fast leg Mar 4-16",  "2020-03-04", "2020-03-16"),
    ("2024 Aug yen-carry unwind",     "2024-07-16", "2024-08-05"),
    ("2025 Apr tariff shock",         "2025-04-02", "2025-04-08"),
]
UNDERSTATE_TOL = 0.0   # ratio < 1 - tol counts as a miss


def stress_table(params: ResponseParams, cm: pd.DataFrame | None = None, spx: pd.Series | None = None,
                 tenors=config.TENORS, episodes=EPISODES) -> pd.DataFrame:
    cm = cm if cm is not None else load_cm()
    spx = spx if spx is not None else load_spx()
    rows = []
    for label, start, end in episodes:
        s, e = pd.Timestamp(start), pd.Timestamp(end)
        if s not in cm.index or e not in cm.index or s not in spx.index or e not in spx.index:
            rows.append({"episode": label, "note": "dates not in data"})
            continue
        r = spx.loc[e] / spx.loc[s] - 1
        ndays = int(cm.index.get_loc(e) - cm.index.get_loc(s))
        row = {"episode": label, "start": start, "end": end, "days": ndays, "spx_ret": r,
               "in_pool": ndays <= config.POOL_MAX_HORIZON}
        for T in tenors:
            real = cm.loc[e, f"cm_{T}"] - cm.loc[s, f"cm_{T}"]
            model = float(params.dvix(r, T))
            row[f"real_{T}"] = real
            row[f"model_{T}"] = model
            row[f"ratio_{T}"] = model / real if real > 0 else float("nan")
        rows.append(row)
    return pd.DataFrame(rows).set_index("episode")


def stress_summary(table: pd.DataFrame, tenors=config.TENORS) -> dict:
    out = {}
    for T in tenors:
        ratio = table[f"ratio_{T}"].dropna()
        out[f"T{T}"] = {"n": len(ratio), "understated": int((ratio < 1 - UNDERSTATE_TOL).sum()),
                        "min_ratio": round(float(ratio.min()), 2), "median_ratio": round(float(ratio.median()), 2)}
    return out


def format_stress(table: pd.DataFrame, tenors=config.TENORS) -> str:
    """Compact display: one block per tenor with realised / model / ratio."""
    head = table[["days", "spx_ret", "in_pool"]].copy()
    head["spx_ret"] = head["spx_ret"].map(lambda x: f"{x:+.1%}")
    blocks = [head]
    for T in tenors:
        b = table[[f"real_{T}", f"model_{T}", f"ratio_{T}"]].round(1)
        b.columns = [f"real{T}", f"mdl{T}", f"x{T}"]
        blocks.append(b)
    return pd.concat(blocks, axis=1).to_string()


if __name__ == "__main__":
    from .response import load_params
    pd.set_option("display.width", 250)
    params = load_params()
    t = stress_table(params)
    print(format_stress(t))
    print("\nsummary (ratio = model / realised; < 1 = model understated):")
    for k, v in stress_summary(t).items():
        print(f"  {k}: {v}")
