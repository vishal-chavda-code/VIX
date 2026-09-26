"""Shared fixtures.  Tests run against the repo's real data and calibration."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# The date the repo's shipped market data and template book are both marked at.
DATA_DATE = "2026-09-18"
TEMPLATE = f"input/book_TEMPLATE_{DATA_DATE}.csv"
VIX_ISSUER = "VOLATILITY INDEX (VIX)"
SPX_ISSUER = "S&P 500 INDEX"


def complete(book: pd.DataFrame) -> pd.DataFrame:
    """Fill the book columns a test does not care about with valid defaults.

    Most tests exercise one thing -- a date format, a strike, the premium > vol precedence --
    and were written before the 2026-09-26 schema added cusip, issuer_name, multiplier,
    premium_source, forward and the src_* columns.  Only columns that are ABSENT are added,
    so a test that removes a required column on purpose still sees it missing."""
    b = book.copy()
    n = len(b)
    defaults = {
        "cusip": [f"T{i:04d}" for i in range(n)],        # unique: it is the position key
        "issuer_name": VIX_ISSUER,
        "multiplier": 100,
        "forward": np.nan,                                # VIX: falls back to the curve (flagged)
        "src_bid": np.nan, "src_ask": np.nan, "src_close": np.nan,
    }
    for col, val in defaults.items():
        if col not in b.columns:
            b[col] = val
    if "premium_source" not in b.columns:
        b["premium_source"] = np.where(b["premium"].notna(), "mid", "") if "premium" in b else ""
    if "premium" not in b.columns and "expiry" in b.columns:
        b["premium"] = np.nan
    return b


@pytest.fixture(scope="session")
def repo() -> Path:
    return ROOT


@pytest.fixture(scope="session")
def params():
    """The calibration of record -- what price.py prices with."""
    from vixshock.response import load_calibration_of_record
    return load_calibration_of_record()[0]


@pytest.fixture(scope="session")
def vov_params():
    from vixshock.response import load_calibration_of_record
    return load_calibration_of_record()[1]


@pytest.fixture(scope="session")
def pooled():
    """The calibration dataset.  Built by run.py into data/processed/ (gitignored), so a fresh
    clone does not have it: the calibration tests skip there rather than fail."""
    if not (ROOT / "data" / "processed" / "dataset_pooled.csv").exists():
        pytest.skip("calibration data not built on this machine -- run `python run.py --no-refresh`")
    from vixshock.join import load_pooled
    return load_pooled()


@pytest.fixture(scope="session")
def market():
    """The market as of the date the repo's data and template book share."""
    from vixshock.shock import load_market
    return load_market(DATA_DATE)


@pytest.fixture
def asof(market):
    return market.asof


@pytest.fixture
def future_expiry(asof) -> str:
    """A valid YYYY-MM-DD expiry comfortably in the future."""
    return (asof + pd.Timedelta(days=61)).strftime("%Y-%m-%d")


@pytest.fixture
def price_book(params, vov_params, market):
    """price_portfolio with the fixtures wired in; takes a DataFrame, fills the columns
    the test does not set (see `complete`)."""
    from vixshock.portfolio import price_portfolio

    def _go(book: pd.DataFrame, **kw):
        return price_portfolio(complete(book), params, vov_params, market, **kw)

    return _go
