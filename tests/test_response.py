"""The response function: shape properties that must hold for any calibration.

These deliberately avoid asserting specific fitted values, which change legitimately
on recalibration. They assert the *structure* — which must not change.
"""
from __future__ import annotations

import numpy as np
import pytest

import config
from vixshock.response import beta_of_tenor, h_down


# ---------------------------------------------------------------- the kernel
def test_slope_is_minus_one_at_zero_for_any_k():
    """h has slope -1 at r=0 whatever k is.

    This is what makes beta interpretable as 'VIX points per unit SPX return for a
    small move'. If it breaks, every stated beta becomes meaningless.
    """
    eps = 1e-7
    for k in (-20.0, -1.0, 0.0, 0.9, 5.0):
        assert h_down(-eps, k) / eps == pytest.approx(1.0, abs=1e-4), f"k={k}"


def test_k_zero_is_the_straight_line():
    r = np.linspace(-0.4, 0, 50)
    assert np.allclose(h_down(r, 0.0), -r)


def test_k_near_zero_is_continuous():
    """No discontinuity at the k=0 branch in the code."""
    r = -0.2
    assert h_down(r, 1e-10) == pytest.approx(h_down(r, 0.0), abs=1e-6)


def test_positive_k_accelerates_negative_k_saturates():
    r = -0.30
    linear = -r
    assert h_down(r, 2.0) > linear      # accelerating
    assert h_down(r, -10.0) < linear    # saturating


def test_saturating_branch_has_the_stated_ceiling():
    """For k < 0 the asymptote is 1/|k|. Module 5 quotes ceilings from this."""
    k = -10.0
    assert h_down(-100.0, k) == pytest.approx(1 / abs(k), rel=1e-6)


def test_beta_of_tenor_decays():
    b = [beta_of_tenor(T, 200.0, 0.2) for T in (30, 60, 90, 120)]
    assert b == sorted(b, reverse=True)
    assert beta_of_tenor(0, 200.0, 0.2) == pytest.approx(200.0)


# ---------------------------------------------------------------- the fitted model
def test_down_moves_raise_vix_up_moves_lower_it(params):
    for T in config.TENORS:
        assert params.dvix(-0.10, T) > 0
        assert params.dvix(+0.10, T) < 0
        assert params.dvix(0.0, T) == pytest.approx(0.0, abs=1e-9)


def test_bigger_drop_bigger_response(params):
    for T in config.TENORS:
        vals = [float(params.dvix(r, T)) for r in (-0.05, -0.10, -0.20, -0.30)]
        assert vals == sorted(vals)


def test_front_reacts_more_than_the_back(params):
    """The tenor-fade dial: 30d must move more than 120d, both directions."""
    assert params.dvix(-0.20, 30) > params.dvix(-0.20, 120)
    assert params.dvix(+0.20, 30) < params.dvix(+0.20, 120)


def test_down_branch_is_harsher_than_up(params):
    """Leverage effect: a -20% drop moves VIX more than a +20% rally does."""
    assert abs(params.dvix(-0.20, 30)) > abs(params.dvix(+0.20, 30))


def test_vectorises_over_r_and_tenor(params):
    r = np.array([-0.2, -0.1, 0.0, 0.1])
    out = params.dvix(r, 30)
    assert out.shape == r.shape
    for i, x in enumerate(r):
        assert out[i] == pytest.approx(float(params.dvix(float(x), 30)))


def test_fitted_params_inside_sane_ranges(params):
    """The same check run.py gates on — caught here so a bad fit fails fast."""
    for name, (lo, hi) in config.PARAM_RANGES.items():
        v = getattr(params, name)
        assert lo <= v <= hi, f"{name}={v} outside [{lo}, {hi}]"


def test_vov_saturates(vov_params):
    """Vol-of-vol must have k < 0. A positive k would mean it accelerates, which the
    data has never supported and which would break the Module 5 explanation."""
    assert vov_params.k < 0
    for name, (lo, hi) in config.PARAM_RANGES_VOV.items():
        v = getattr(vov_params, name)
        assert lo <= v <= hi, f"{name}={v} outside [{lo}, {hi}]"


def test_calibration_is_not_stale_beyond_policy(params):
    """The fit window should reach recent data. Catches a params file left behind."""
    import pandas as pd
    assert pd.Timestamp(params.fit_end) > pd.Timestamp("2024-01-01")
    assert params.n_obs > 50_000
