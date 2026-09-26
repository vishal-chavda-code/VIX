"""The VIX-only book must price exactly as it did before the 2026-09-26 rebuild, times the multiplier.

tests/regression/ holds two files written by the PRE-CHANGE pricer (shock.reprice_book, since
removed), before any code was edited:

    vix_only_book_pre_change.csv       13 VIX options chosen to hit every path: premium-priced,
                                       vol-priced, ATM fallback, book forward vs curve forward, and
                                       a ~400-day option beyond the last calibrated tenor
    vix_only_snapshot_pre_change.csv   its output as of 2026-09-18 on the old -40..+40% grid

The book is converted to the new schema by adding only the new columns.  `forward` is set to
the forward the old code used for each position, so both pricers see the same inputs.  Old
P&L was in index points; new P&L is in currency, so new = old * 100.

The calibration is pinned too: response_params_pre_change.json and vov_params_pre_change.json
are the parameter files the snapshot was priced with (least-squares up branch).  So this test
measures the PRICING LAYER alone, and a recalibration -- such as the 2026-09-26 one that made
the up branch an envelope -- does not break it.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

REG = "tests/regression"


@pytest.fixture(scope="module")
def compared(repo):
    from vixshock.portfolio import price_portfolio
    from vixshock.response import ResponseParams
    from vixshock.shock import load_market

    params = ResponseParams.from_json(repo / REG / "response_params_pre_change.json")
    vov_params = ResponseParams.from_json(repo / REG / "vov_params_pre_change.json")

    old_book = pd.read_csv(repo / REG / "vix_only_book_pre_change.csv")
    snap = pd.read_csv(repo / REG / "vix_only_snapshot_pre_change.csv")
    b = old_book.copy()
    b.insert(0, "cusip", [f"REG{i:03d}" for i in b.index])
    b["issuer_name"] = "VOLATILITY INDEX (VIX)"
    b["multiplier"] = 100
    b["forward"] = snap.drop_duplicates("position").set_index("position")["fwd"].reindex(b.index).to_numpy()
    b["premium_source"] = np.where(b["premium"].notna(), "mid", "")
    b["src_bid"] = b["src_ask"] = b["src_close"] = np.nan

    # Market inputs are pinned to what the old pricer saw, so only the pricing layer is under
    # test: forwards above, and here the ATM fallback vol (the old pricer read it from a
    # run.py-built file; the market now supplies VVIX).  One position uses it.
    from vixshock.shock import Curve
    m = load_market("2026-09-18")
    fb = snap.loc[snap["vol_source"] == "ATM curve (fallback)", "vol"].unique()
    assert len(fb) == 1
    m.vov_curve = Curve({30: float(fb[0])})
    new = price_portfolio(b, params, vov_params, m, warn=lambda m: None)
    new["position"] = new["cusip"].str[3:].astype(int)
    return snap.merge(new, on=["position", "scenario"], suffixes=("_old", "_new"))


def test_every_new_scenario_is_in_the_snapshot(compared, repo):
    import config
    assert set(compared["scenario"]) == {f"{s:+.0%}" for s in config.SHOCKS}
    assert len(compared) == 13 * len(config.SHOCKS)


@pytest.mark.parametrize("col", ["pnl_vol_fixed", "pnl_vol_shocked"])
def test_pnl_is_old_pnl_times_multiplier(compared, col):
    np.testing.assert_allclose(compared[f"{col}_new"], compared[f"{col}_old"] * 100, rtol=1e-12, atol=1e-8)


@pytest.mark.parametrize("col", ["base_price", "vol", "fwd_shocked", "vol_shocked"])
def test_intermediate_values_unchanged(compared, col):
    np.testing.assert_allclose(compared[f"{col}_new"], compared[f"{col}_old"], rtol=1e-12, atol=1e-12)


def test_vol_sources_unchanged(compared):
    assert (compared["vol_source_new"] == compared["vol_source_old"]).all()
