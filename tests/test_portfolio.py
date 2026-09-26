"""The pricing layer: SPX routing, per-product floors, the SPX vol shock, aggregation.

Written against the rules in vixshock/portfolio.py's docstring.  Structural where possible,
so a legitimate recalibration does not break them.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import config
from conftest import SPX_ISSUER, TEMPLATE, VIX_ISSUER, complete
from vixshock.pricing import black76
from vixshock.validate import BookError, read_book

SPX_PUT = {"cusip": "S1", "issuer_name": SPX_ISSUER, "expiry": "2026-12-18", "strike": 6900, "type": "P",
           "quantity": 20, "multiplier": 100, "premium": 30.70, "premium_source": "mid",
           "src_bid": 30.2, "src_ask": 31.2, "src_close": 30.7, "forward": np.nan}
VIX_CALL = {"cusip": "V1", "issuer_name": VIX_ISSUER, "expiry": "2026-10-21", "strike": 20, "type": "C",
            "quantity": 100, "multiplier": 100, "premium": 1.25, "premium_source": "mid",
            "src_bid": 1.2, "src_ask": 1.3, "src_close": 1.25, "forward": 18.0411}


def rows(*dicts, **over):
    return pd.DataFrame([{**d, **over} for d in dicts])


def at(detail, cusip, scenario):
    return detail[(detail["cusip"] == cusip) & (detail["scenario"] == scenario)].iloc[0]


# ---------------------------------------------------------------- SPX routing (P2)
def test_spx_forward_is_close_times_carry(price_book, market):
    d = price_book(rows(SPX_PUT))
    r0 = d.iloc[0]
    expect = market.spx * np.exp((config.RISK_FREE_RATE - config.SPX_DIVIDEND_YIELD) * r0["tenor"] / 365)
    assert r0["forward"] == pytest.approx(expect, rel=1e-12)
    assert r0["forward_source"] == "SPX close + carry"


def test_spx_book_forward_wins_when_given(price_book):
    d = price_book(rows(SPX_PUT, forward=7700.0))
    assert d.iloc[0]["forward"] == 7700.0 and d.iloc[0]["forward_source"] == "book"


@pytest.mark.parametrize("s", config.SHOCKS)
def test_spx_shocked_forward_is_the_scenario_itself(price_book, s):
    """No response function on the SPX leg: the scenario IS the move."""
    r0 = at(price_book(rows(SPX_PUT)), "S1", f"{s:+.0%}")
    assert r0["fwd_shocked"] == pytest.approx(r0["forward"] * (1 + s), rel=1e-12)
    assert not r0["vix_floor_bound"]


def test_vix_leg_uses_the_response_function(price_book, params):
    r0 = at(price_book(rows(VIX_CALL)), "V1", "-10%")
    assert r0["fwd_shocked"] == pytest.approx(r0["forward"] + params.dvix(-0.10, r0["tenor"]), rel=1e-12)


def test_spx_base_price_reproduces_the_mark(price_book):
    """The inverter stops at a 1e-8 vol tolerance; SPX vega is ~250x VIX vega, so the residual
    is ~2e-6 index points here -- under half a cent on the whole position."""
    assert price_book(rows(SPX_PUT)).iloc[0]["base_price"] == pytest.approx(30.70, abs=1e-4)


def test_spx_without_a_premium_never_uses_the_vix_vol_curve(price_book):
    """The ATM fallback curve is VIX-option vol (~90%): pricing an SPX put on it is garbage.
    An SPX position with no premium falls back to VOL_FLOOR_SPX instead, flagged."""
    from vixshock.portfolio import SPX_FALLBACK
    r0 = price_book(rows(SPX_PUT, premium=np.nan, premium_source="")).iloc[0]
    assert r0["vol_source"] == SPX_FALLBACK and r0["vol"] == config.VOL_FLOOR_SPX


def test_deep_itm_spx_put_below_carry_intrinsic_prices_instead_of_rejecting_the_book(price_book, market):
    """In a selloff a deep ITM put can sit below intrinsic against the flat-carry forward.  The
    book must still price -- that position flagged, the rest normal."""
    from vixshock.portfolio import SPX_FALLBACK
    below = round((8400 - market.spx * 1.007) * 0.99 - 5, 2)          # under our discounted intrinsic
    d = price_book(rows(SPX_PUT, {**SPX_PUT, "cusip": "ITM", "strike": 8400, "premium": below,
                                  "src_bid": np.nan, "src_ask": np.nan, "src_close": np.nan}))
    assert at(d, "ITM", "+5%")["vol_source"] == SPX_FALLBACK
    assert at(d, "S1", "+5%")["vol_source"] == "from premium"


def test_a_floor_never_pushes_vol_up(price_book):
    """A position already below VOL_FLOOR_SPX must not have its vol RAISED by a rally shock."""
    d = price_book(rows(SPX_PUT, vol=0.03, premium=np.nan, premium_source=""))     # book vol 3%
    for s in config.SHOCKS:
        r0 = at(d, "S1", f"{s:+.0%}")
        if r0["vol_shock"] < 0:
            assert r0["vol_shocked"] <= r0["vol"] + 1e-12


def test_mixed_book_routes_each_row(price_book):
    d = price_book(rows(SPX_PUT, VIX_CALL))
    assert set(d["underlying"]) == {"VIX", "SPX"}
    assert (d[d.underlying == "VIX"]["forward_source"] == "book").all()


# ---------------------------------------------------------------- the SPX vol shock (P3)
@pytest.mark.parametrize("s", config.SHOCKS)
def test_spx_vol_shock_is_the_vix_response_averaged_over_the_options_life(price_book, params, s):
    """The closed form must equal a brute-force average of the fitted response over tenors
    0..T-30 -- the VIX futures whose 30-day windows tile the option's life."""
    r0 = at(price_book(rows(SPX_PUT)), "S1", f"{s:+.0%}")
    t = np.linspace(0.0, r0["tenor"] - 30.0, 200_001)
    brute = config.SPX_VOL_SCALE * np.mean(params.dvix(s, t)) / 100
    assert r0["vol_shock"] == pytest.approx(brute, rel=1e-6)


@pytest.mark.parametrize("s", [-0.10, 0.10])
def test_spx_vol_shock_up_to_one_month_is_the_spot_response(params, s):
    """An option with 30 days or less lives inside spot VIX's own window: tenor 0, no averaging."""
    from vixshock.portfolio import spx_vol_shock
    for T in (1, 7, 30):
        assert spx_vol_shock(params, s, T) == pytest.approx(config.SPX_VOL_SCALE * params.dvix(s, 0.0) / 100)


@pytest.mark.parametrize("s", [-0.10, 0.10])
def test_spx_vol_shock_exceeds_the_old_endpoint_mapping_past_one_month(params, s):
    """The old mapping read dVIX at tenor T: the far end, where the response has faded most."""
    from vixshock.portfolio import spx_vol_shock
    for T in (60, 91, 182, 365):
        assert abs(spx_vol_shock(params, s, T)) > abs(params.dvix(s, T) / 100)


def test_spx_vol_shock_is_a_parallel_shift_of_the_positions_own_vol(price_book):
    """Two strikes, same expiry: same shift, different base vols (the skew is held)."""
    d = price_book(rows(SPX_PUT, {**SPX_PUT, "cusip": "S2", "strike": 6500, "premium": 12.0,
                                  "src_bid": 11.7, "src_ask": 12.3, "src_close": 12.0}))
    a, b = at(d, "S1", "+10%"), at(d, "S2", "+10%")
    assert a["vol"] != pytest.approx(b["vol"])
    assert a["vol_shock"] == pytest.approx(b["vol_shock"])


def test_rally_lowers_spx_vol_and_selloff_raises_it(price_book):
    d = price_book(rows(SPX_PUT))
    assert at(d, "S1", "+10%")["vol_shocked"] < at(d, "S1", "+10%")["vol"]
    assert at(d, "S1", "-10%")["vol_shocked"] > at(d, "S1", "-10%")["vol"]


def test_long_put_loses_in_a_rally_and_gains_in_a_selloff(price_book):
    d = price_book(rows(SPX_PUT))
    assert at(d, "S1", "+10%")["pnl_vol_shocked"] < 0 < at(d, "S1", "-10%")["pnl_vol_shocked"]


# ---------------------------------------------------------------- floors (P0.2, P0.3)
def test_vol_floors_are_per_product():
    assert config.VOL_FLOOR_SPX < config.VOL_FLOOR_VIX
    assert config.VOL_FLOOR_SPX < 0.10        # below any calm-tape SPX ATM vol, or the rally is truncated


def test_spx_vol_floor_binds_and_is_flagged(params, vov_params, market):
    """A low-vol SPX put in a big rally: the floor must bind at VOL_FLOOR_SPX, not the VIX floor."""
    from vixshock.portfolio import price_portfolio
    tiny = float(black76(market.spx * 1.007, 7400, 91 / 365, 0.06, config.RISK_FREE_RATE, False))
    b = rows(SPX_PUT, strike=7400, premium=round(tiny, 2), src_bid=np.nan, src_ask=np.nan, src_close=np.nan)
    d = price_portfolio(b, params, vov_params, market, warn=lambda m: None)
    r0 = at(d, "S1", "+20%")
    assert r0["vol_floor_bound"]
    assert r0["vol_shocked"] == pytest.approx(config.VOL_FLOOR_SPX)


def test_vix_floor_binds_in_a_big_rally_and_is_reported(price_book, params):
    from vixshock.portfolio import floor_bindings
    d = price_book(rows(VIX_CALL))
    r0 = at(d, "V1", "+20%")
    if r0["forward"] + params.dvix(0.20, r0["tenor"]) < config.VIX_FLOOR:
        assert r0["vix_floor_bound"] and r0["fwd_shocked"] == config.VIX_FLOOR
        b = floor_bindings(d)
        assert ((b["cusip"] == "V1") & (b["floor"] == "VIX_FLOOR") & (b["scenario"] == "+20%")).any()


def test_floor_effect_is_zero_when_nothing_binds(price_book):
    from vixshock.portfolio import NO_FLOORS, floor_report
    d = price_book(rows(VIX_CALL))
    u = price_book(rows(VIX_CALL), floors=NO_FLOORS)
    rep = floor_report(d, u)
    quiet = rep[(rep["VIX_FLOOR n"] == 0) & (rep["VOL_FLOOR_VIX n"] == 0) & (rep["VOL_FLOOR_SPX n"] == 0)]
    assert len(quiet) > 0
    assert (quiet["floor_effect vol shocked"] == 0).all()


def test_floors_are_config_not_buried_in_code():
    import vixshock.portfolio as pf
    assert pf.FLOORS == {"vix": config.VIX_FLOOR, "VIX": config.VOL_FLOOR_VIX, "SPX": config.VOL_FLOOR_SPX}


# ---------------------------------------------------------------- aggregation (P4)
def test_pnl_is_in_currency(price_book, params):
    d = price_book(rows(VIX_CALL))
    r0 = at(d, "V1", "-10%")
    assert r0["pnl_vol_fixed"] == pytest.approx((r0["price_vol_fixed"] - r0["base_price"]) * 100 * 100, rel=1e-12)


def test_summary_subtotals_add_up(repo, price_book):
    from vixshock.portfolio import summarize
    d = price_book(read_book(repo / TEMPLATE))
    s = summarize(d)
    assert list(s.index) == [f"{x:+.0%}" for x in config.SHOCKS]
    for kind in ("vol_fixed", "vol_shocked"):
        np.testing.assert_allclose(s[f"VIX_{kind}"] + s[f"SPX_{kind}"], s[f"total_{kind}"], atol=0.02)
        for u in ("VIX", "SPX"):
            by_hand = d[d.underlying == u].groupby("scenario")[f"pnl_{kind}"].sum().reindex(s.index)
            np.testing.assert_allclose(s[f"{u}_{kind}"], by_hand, atol=0.01)
    np.testing.assert_allclose(s["vol_of_vol_effect"], s["total_vol_shocked"] - s["total_vol_fixed"], atol=0.02)


def test_summary_of_a_vix_only_book_has_zero_spx(price_book):
    from vixshock.portfolio import summarize
    s = summarize(price_book(rows(VIX_CALL)))
    assert (s["SPX_vol_fixed"] == 0).all() and (s["SPX_vol_shocked"] == 0).all()


def test_scenario_grid_is_the_trimmed_one():
    assert config.SHOCKS == (-0.20, -0.15, -0.10, -0.05, 0.05, 0.10, 0.15, 0.20)


def test_position_beyond_last_tenor_is_flagged(price_book):
    d = price_book(rows(SPX_PUT, cusip="L1", expiry="2027-12-17", premium=250.0, src_bid=np.nan,
                        src_ask=np.nan, src_close=np.nan))
    assert d["beyond_calibrated_tenor"].all()


# ---------------------------------------------------------------- VIX forward = that expiry's future
def test_vix_forward_is_the_settle_of_the_future_expiring_with_it(price_book, market):
    """A lookup, not an interpolation: the Oct option's forward is the Oct future's settle."""
    r0 = price_book(rows(VIX_CALL, forward=np.nan)).iloc[0]
    assert r0["forward_source"] == "VX settle"
    assert r0["forward"] == market.vx_settles[pd.Timestamp("2026-10-21")]
    assert r0["forward"] == pytest.approx(18.0411, abs=1e-4)          # not the curve's 17.84


def test_vix_weekly_without_a_forward_falls_back_and_is_flagged(price_book):
    from vixshock.portfolio import CURVE_FALLBACK
    r0 = price_book(rows(VIX_CALL, expiry="2026-10-28", forward=np.nan)).iloc[0]   # no listed future that day
    assert r0["forward_source"] == CURVE_FALLBACK


def test_book_forward_overrides_the_settle(price_book):
    r0 = price_book(rows(VIX_CALL, forward=18.50)).iloc[0]
    assert r0["forward"] == 18.50 and r0["forward_source"] == "book"


def test_settles_are_only_from_the_pricing_date(market):
    """No settle is carried forward from an earlier day."""
    assert market.vx_settles and all(isinstance(k, pd.Timestamp) for k in market.vx_settles)
    assert len(market.vx_settles) == 8            # every contract listed on 2026-09-18
