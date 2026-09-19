"""Shared fixtures.  Tests run against the repo's real data and calibration."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="session")
def repo() -> Path:
    return ROOT


@pytest.fixture(scope="session")
def params():
    from vixshock.response import load_params
    return load_params()


@pytest.fixture(scope="session")
def vov_params():
    from vixshock.volofvol import load_vov_params
    return load_vov_params()


@pytest.fixture(scope="session")
def curves():
    """(asof, vix_curve, vov_curve, spot) as of the latest date on disk."""
    from vixshock import shock
    return shock.latest_curves()


@pytest.fixture
def asof(curves):
    return curves[0]


@pytest.fixture
def future_expiry(asof) -> str:
    """A valid YYYY-MM-DD expiry comfortably in the future."""
    return (asof + pd.Timedelta(days=61)).strftime("%Y-%m-%d")


@pytest.fixture
def price_book(params, vov_params, curves):
    """reprice_book with the fixtures already wired in; takes a DataFrame."""
    from vixshock import shock
    asof_, vix_curve, vov_curve, _ = curves

    def _go(book: pd.DataFrame, **kw):
        return shock.reprice_book(book, params, vov_params, vix_curve, vov_curve, asof_, **kw)

    return _go
