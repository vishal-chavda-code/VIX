"""Black-76 and the implied-vol inverter.

These test mathematical identities that must hold regardless of calibration, so they
are the tests most likely to catch a regression from a dependency upgrade.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from vixshock.pricing import black76, implied_vol

R = 0.04


# ---------------------------------------------------------------- identities
def test_put_call_parity():
    """C - P == discount * (F - K).  Must hold to machine precision at every strike."""
    F, T, sig = 19.37, 0.25, 0.9
    for K in (5.0, 15.0, 19.37, 25.0, 50.0, 100.0):
        c = float(black76(F, K, T, sig, R, True))
        p = float(black76(F, K, T, sig, R, False))
        assert c - p == pytest.approx(math.exp(-R * T) * (F - K), abs=1e-10), f"K={K}"


def test_price_is_monotone_in_vol():
    """A call is worth more at a higher vol.  Always."""
    prices = [float(black76(19.37, 25.0, 0.25, v, R, True)) for v in (0.2, 0.5, 0.9, 1.5, 2.0)]
    assert prices == sorted(prices)
    assert len(set(prices)) == len(prices)


def test_price_is_monotone_in_forward():
    """A call gains as the forward rises; a put loses."""
    calls = [float(black76(F, 25.0, 0.25, 0.9, R, True)) for F in (10, 15, 20, 30, 50)]
    puts = [float(black76(F, 25.0, 0.25, 0.9, R, False)) for F in (10, 15, 20, 30, 50)]
    assert calls == sorted(calls)
    assert puts == sorted(puts, reverse=True)


def test_never_below_discounted_intrinsic():
    for F, K in ((30.0, 20.0), (20.0, 30.0), (19.37, 19.37)):
        for is_call in (True, False):
            intrinsic = max(F - K, 0) if is_call else max(K - F, 0)
            px = float(black76(F, K, 0.25, 0.9, R, is_call))
            assert px >= intrinsic * math.exp(-R * 0.25) - 1e-9


def test_deep_otm_is_tiny_but_finite():
    """The far-OTM case the book actually holds: must not be nan or negative."""
    px = float(black76(19.37, 100.0, 91 / 365, 0.725, R, True))
    assert np.isfinite(px)
    assert 0 <= px < 0.001


def test_vectorised_matches_scalar():
    F = np.array([19.0, 20.0, 21.0])
    K = np.array([20.0, 20.0, 20.0])
    vec = black76(F, K, 0.25, 0.9, R, True)
    for i in range(3):
        assert vec[i] == pytest.approx(float(black76(F[i], K[i], 0.25, 0.9, R, True)))


# ---------------------------------------------------------------- the inverter
@pytest.mark.parametrize("vol_in", [0.25, 0.60, 0.925, 1.27, 2.0])
def test_implied_vol_round_trips(vol_in):
    """price(vol) -> implied_vol(price) must return the vol you started with.

    This is the mechanism the whole premium-first book input depends on.
    """
    F, K, T = 19.37, 25.0, 91 / 365
    px = float(black76(F, K, T, vol_in, R, True))
    assert implied_vol(px, F, K, T, R, True) == pytest.approx(vol_in, abs=1e-4)


def test_implied_vol_round_trips_far_otm():
    """The 4-cent 100-strike call from the findings: 0.04 -> ~127% -> 0.04."""
    F, K, T = 19.37, 100.0, 91 / 365
    v = implied_vol(0.04, F, K, T, R, True)
    assert 1.2 < v < 1.35
    assert float(black76(F, K, T, v, R, True)) == pytest.approx(0.04, abs=1e-6)


def test_implied_vol_returns_nan_below_intrinsic():
    """A price below intrinsic is an arbitrage; the inverter must not invent a vol."""
    assert math.isnan(implied_vol(0.5, 30.0, 20.0, 0.25, R, True))


def test_forward_convention_matters():
    """Using spot instead of the forward gives a materially different vol.

    This is the measured claim in input/INPUT_CONTRACT.md; if it stops holding the
    documentation is wrong.
    """
    K, T, MKT = 100.0, 91 / 365, 0.04
    correct = implied_vol(MKT, 19.37, K, T, R, True)
    off_spot = implied_vol(MKT, 17.71, K, T, R, True)
    assert off_spot - correct > 0.05          # at least 5 vol points higher
    mispriced = float(black76(19.37, K, T, off_spot, R, True))
    assert mispriced / MKT > 1.4              # at least 40% too expensive
