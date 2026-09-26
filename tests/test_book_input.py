"""Book input handling: validation, and the premium > vol > ATM precedence.

Every rejection test here corresponds to an input that the model ACCEPTED before
2026-09-19 and priced to a wrong number. They are regression tests for real bugs,
not hypotheticals.
"""
from __future__ import annotations

import io

import pandas as pd
import pytest

from conftest import complete
from vixshock.validate import BookError, validate_book


def book(csv: str) -> pd.DataFrame:
    return pd.read_csv(io.StringIO(csv))


# ---------------------------------------------------------------- rejections
def test_rejects_negative_strike(price_book, future_expiry):
    with pytest.raises(BookError, match="strike"):
        price_book(book(f"expiry,strike,type,quantity,premium\n{future_expiry},-20,C,100,1.0\n"))


def test_rejects_zero_strike(price_book, future_expiry):
    """Priced at 18.56 before validation existed."""
    with pytest.raises(BookError, match="strike"):
        price_book(book(f"expiry,strike,type,quantity,premium\n{future_expiry},0,C,100,1.0\n"))


def test_rejects_unknown_type(price_book, future_expiry):
    """'X' was silently treated as a PUT."""
    with pytest.raises(BookError, match="type"):
        price_book(book(f"expiry,strike,type,quantity,premium\n{future_expiry},20,X,100,1.0\n"))


def test_rejects_negative_premium(price_book, future_expiry):
    with pytest.raises(BookError, match="premium"):
        price_book(book(f"expiry,strike,type,quantity,premium\n{future_expiry},20,C,100,-1.0\n"))


def test_rejects_zero_vol_sentinel(price_book, future_expiry):
    """vol=0 priced the option at zero volatility, silently returning 0.000000."""
    with pytest.raises(BookError, match="vol"):
        price_book(book(f"expiry,strike,type,quantity,premium,vol\n{future_expiry},20,C,100,,0\n"))


def test_rejects_non_numeric_quantity(price_book, future_expiry):
    with pytest.raises(BookError, match="quantity"):
        price_book(book(f"expiry,strike,type,quantity,premium\n{future_expiry},20,C,lots,1.0\n"))


def test_rejects_empty_book(price_book):
    with pytest.raises(BookError, match="empty"):
        price_book(book("expiry,strike,type,quantity,premium\n"))


def test_rejects_missing_required_column(price_book, future_expiry):
    with pytest.raises(BookError, match="type"):
        price_book(book(f"expiry,strike,quantity\n{future_expiry},20,100\n"))


def test_rejects_expired_option(price_book):
    with pytest.raises(BookError, match="expire"):
        price_book(book("expiry,strike,type,quantity,premium\n2020-01-15,20,C,100,1.0\n"))


# ---------------------------------------------------------------- dates
def test_rejects_ambiguous_date(price_book):
    """01/02/2027 is 2 Jan under a US reading and 1 Feb under a European one.
    Guessing wrong shifts the tenor by a month with no warning."""
    with pytest.raises(BookError, match="YYYY-MM-DD"):
        price_book(book("expiry,strike,type,quantity,premium\n01/02/2027,20,C,100,1.0\n"))


@pytest.mark.parametrize("bad", ["11/18/2026", "18/11/2026", "18-Nov-2026", "20261118", "46345"])
def test_rejects_every_non_iso_date(price_book, bad):
    with pytest.raises(BookError, match="YYYY-MM-DD"):
        price_book(book(f"expiry,strike,type,quantity,premium\n{bad},20,C,100,1.0\n"))


def test_accepts_iso_date_with_whitespace(price_book, future_expiry):
    d = price_book(book(f"expiry,strike,type,quantity,premium\n  {future_expiry} ,20,C,100,1.0\n"))
    assert len(d) > 0


# ---------------------------------------------------------------- type normalisation
@pytest.mark.parametrize("raw,expect", [
    ("C", "C"), (" C", "C"), ("c ", "C"), ("Call", "C"), ("call", "C"),
    ("P", "P"), (" p", "P"), ("PUT", "P"),
])
def test_type_whitespace_and_case(price_book, future_expiry, raw, expect):
    """A stray space used to flip every call in the book to a put."""
    d = price_book(book(f'expiry,strike,type,quantity,premium\n{future_expiry},20,"{raw}",100,1.0\n'))
    assert d.iloc[0]["type"] == expect


# ---------------------------------------------------------------- vol precedence
def test_premium_is_used_when_only_premium_given(price_book, future_expiry):
    d = price_book(book(f"expiry,strike,type,quantity,premium\n{future_expiry},20,C,100,1.0\n"))
    assert d.iloc[0]["vol_source"] == "from premium"
    assert d.iloc[0]["base_price"] == pytest.approx(1.0, abs=1e-6)


def test_blank_vol_column_is_same_as_no_column(price_book, future_expiry):
    a = price_book(book(f"expiry,strike,type,quantity,premium\n{future_expiry},20,C,100,1.0\n"))
    b = price_book(book(f"expiry,strike,type,quantity,premium,vol\n{future_expiry},20,C,100,1.0,\n"))
    assert a.iloc[0]["vol"] == pytest.approx(b.iloc[0]["vol"])
    assert b.iloc[0]["vol_source"] == "from premium"


def test_vol_used_when_no_premium(price_book, future_expiry):
    d = price_book(book(f"expiry,strike,type,quantity,vol\n{future_expiry},20,C,100,1.2\n"))
    assert d.iloc[0]["vol_source"] == "book vol"
    assert d.iloc[0]["vol"] == pytest.approx(1.2)


def test_premium_beats_vol_when_both_given(price_book, future_expiry):
    """The precedence that makes the mark exact rather than convention-dependent."""
    d = price_book(book(f"expiry,strike,type,quantity,premium,vol\n{future_expiry},20,C,100,1.0,1.2\n"))
    assert d.iloc[0]["vol_source"] == "from premium"
    assert d.iloc[0]["vol"] != pytest.approx(1.2)
    assert d.iloc[0]["base_price"] == pytest.approx(1.0, abs=1e-6)


def test_atm_fallback_is_flagged(price_book, future_expiry):
    """The fallback that marks far-OTM calls near zero must be visible in the output."""
    d = price_book(book(f"expiry,strike,type,quantity\n{future_expiry},20,C,100\n"))
    assert d.iloc[0]["vol_source"] == "ATM curve (fallback)"


def test_far_otm_fallback_prices_near_zero(price_book, future_expiry):
    """Documents the failure the book_vols gate exists to prevent."""
    d = price_book(book(f"expiry,strike,type,quantity\n{future_expiry},100,C,-50\n"))
    assert d.iloc[0]["base_price"] < 0.001
    assert d.iloc[0]["vol_source"] == "ATM curve (fallback)"


# ---------------------------------------------------------------- warnings, not errors
def test_non_wednesday_expiry_warns_but_prices(asof):
    """VIX options expire on a Wednesday; anything else is usually a bad parse."""
    import pandas as pd
    d = asof + pd.Timedelta(days=60)
    d += pd.Timedelta(days=(3 - d.dayofweek) % 7 or 1)      # force a non-Wednesday
    msgs = []
    out = validate_book(
        complete(book(f"expiry,strike,type,quantity,premium\n{d:%Y-%m-%d},20,C,100,1.0\n")),
        asof, warn=msgs.append)
    assert len(out) == 1
    if d.dayofweek != 2:
        assert any("Wednesday" in m for m in msgs)


def test_duplicate_rows_warn(asof, future_expiry):
    msgs = []
    row = f"{future_expiry},20,C,100,1.0"
    validate_book(complete(book(f"expiry,strike,type,quantity,premium\n{row}\n{row}\n")), asof, warn=msgs.append)
    assert any("share an expiry" in m for m in msgs)


def test_percentage_vol_warns(asof, future_expiry):
    """127 instead of 1.27 is a plausible mistake and should be called out."""
    msgs = []
    validate_book(complete(book(f"expiry,strike,type,quantity,vol\n{future_expiry},20,C,100,127\n")),
                  asof, warn=msgs.append)
    assert any("DECIMAL" in m for m in msgs)
