"""The 2026-09-26 book schema: product, key, provenance, and the pricing date.

Each rejection here is an input that would otherwise have priced -- to the wrong size, under
the wrong key, or as of the wrong day -- without any error.
"""
from __future__ import annotations

import io

import numpy as np
import pandas as pd
import pytest

from conftest import DATA_DATE, SPX_ISSUER, TEMPLATE, VIX_ISSUER, complete
from vixshock.validate import BookError, asof_from_filename, read_book, validate_book


def one(asof, **over) -> pd.DataFrame:
    """A single valid VIX row, with any column overridden."""
    row = {"cusip": "C1", "issuer_name": VIX_ISSUER, "expiry": "2026-11-18", "strike": 20, "type": "C",
           "quantity": 10, "multiplier": 100, "premium": 1.0, "premium_source": "mid",
           "src_bid": 0.95, "src_ask": 1.05, "src_close": 1.0, "forward": 18.7}
    row.update(over)
    return pd.DataFrame([row])


def check(asof, book, warn=None):
    return validate_book(book, asof, warn=warn or (lambda m: None))


# ---------------------------------------------------------------- product
def test_underlying_is_derived_from_issuer_name(asof):
    b = pd.concat([one(asof), one(asof, cusip="C2", issuer_name=SPX_ISSUER, strike=6500, type="P",
                                    expiry="2026-12-18", premium=12.0, src_bid=11.9, src_ask=12.1,
                                    src_close=12.0, forward=np.nan)])
    out = check(asof, b.reset_index(drop=True))
    assert list(out["underlying"]) == ["VIX", "SPX"]


@pytest.mark.parametrize("name", ["volatility index (vix)", "  VOLATILITY  INDEX (VIX) "])
def test_issuer_name_tolerates_case_and_spacing(asof, name):
    assert check(asof, one(asof, issuer_name=name))["underlying"].iloc[0] == "VIX"


@pytest.mark.parametrize("name", ["S&P 500 MINI INDEX", "VIX", "", np.nan])
def test_unrecognised_issuer_fails_and_is_never_defaulted(asof, name):
    with pytest.raises(BookError, match="issuer_name"):
        check(asof, one(asof, issuer_name=name))


def test_underlying_column_if_sent_must_agree(asof):
    with pytest.raises(BookError, match="underlying"):
        check(asof, one(asof, underlying="SPX"))


@pytest.mark.parametrize("mult", [10, 1000])
def test_multiplier_must_match_the_product_table(asof, mult):
    """XSP or a mini contract mislabelled as the full-size product must not price at 100x."""
    with pytest.raises(BookError, match="multiplier"):
        check(asof, one(asof, multiplier=mult))


@pytest.mark.parametrize("how", ["blank", "absent"])
def test_multiplier_comes_from_the_product_table_when_not_sent(asof, how):
    b = one(asof, multiplier=np.nan) if how == "blank" else one(asof).drop(columns="multiplier")
    assert check(asof, b)["multiplier"].iloc[0] == 100


# ---------------------------------------------------------------- key
def test_cusip_must_be_unique(asof):
    b = pd.concat([one(asof), one(asof, strike=25)]).reset_index(drop=True)
    with pytest.raises(BookError, match="cusip"):
        check(asof, b)


def test_cusip_must_not_be_blank(asof):
    with pytest.raises(BookError, match="cusip"):
        check(asof, one(asof, cusip="  "))


def test_read_book_keeps_leading_zeros_in_cusip(tmp_path, repo):
    """An all-digit CUSIP read as a number loses its leading zero and stops matching."""
    text = (repo / TEMPLATE).read_text().replace("TPLVIX001", "037833100")
    p = tmp_path / f"book_{DATA_DATE}.csv"
    p.write_text(text)
    assert "037833100" in set(read_book(p)["cusip"])


# ---------------------------------------------------------------- removed columns
def test_lots_of_one_is_ignored(asof):
    out = check(asof, one(asof, lots=1))
    assert "lots" not in out.columns


def test_lots_other_than_one_fails(asof):
    """lots != 1 would mean quantity is not the whole position; ignoring it would understate."""
    with pytest.raises(BookError, match="lots"):
        check(asof, one(asof, lots=5))


def test_vol_column_is_optional(asof):
    assert "vol" in check(asof, one(asof)).columns


# ---------------------------------------------------------------- provenance
@pytest.mark.parametrize("src", ["premium", "mid", "ASK", " close ", "bid"])
def test_premium_source_accepts_the_named_fields(asof, src):
    prem = {"premium": 1.0, "mid": 1.0, "ask": 1.05, "close": 1.0, "bid": 0.95}[src.strip().lower()]
    assert check(asof, one(asof, premium_source=src, premium=prem))["premium_source"].iloc[0] == src.strip().lower()


@pytest.mark.parametrize("src", ["", np.nan, "absent"])
def test_premium_source_defaults_to_the_feeds_own_premium(asof, src):
    """Blank, NaN or no column at all = the feed's premium field as delivered."""
    b = one(asof).drop(columns="premium_source") if src == "absent" else one(asof, premium_source=src)
    assert check(asof, b)["premium_source"].iloc[0] == "premium"


@pytest.mark.parametrize("src", ["last", "theo", "settle"])
def test_premium_source_rejects_unknown_fields(asof, src):
    with pytest.raises(BookError, match="premium_source"):
        check(asof, one(asof, premium_source=src))


def test_premium_source_premium_makes_no_claim_so_is_not_cross_checked(asof):
    msgs = []
    check(asof, one(asof, premium_source="premium", premium=1.03), warn=msgs.append)   # matches no src_*
    assert not any("premium_source" in m for m in msgs)


def test_premium_source_mismatch_warns(asof):
    msgs = []
    check(asof, one(asof, premium_source="ask", premium=1.0), warn=msgs.append)   # ask is 1.05
    assert any("premium_source" in m for m in msgs)


def test_zero_bid_is_derived_from_src_bid(asof):
    b = pd.concat([one(asof, src_bid=0.0, src_ask=0.08, premium=0.04), one(asof, cusip="C2")]).reset_index(drop=True)
    assert list(check(asof, b)["zero_bid"]) == [True, False]


def test_template_carries_one_zero_bid_row(repo, asof):
    out = check(asof, read_book(repo / TEMPLATE))
    assert int(out["zero_bid"].sum()) == 1


@pytest.mark.parametrize("col", ["cusip", "issuer_name",
                                 "src_bid", "src_ask", "src_close"])
def test_new_columns_are_required(asof, col):
    with pytest.raises(BookError, match=col):
        check(asof, one(asof).drop(columns=col))


# ---------------------------------------------------------------- the pricing date
@pytest.mark.parametrize("name,expect", [
    ("book_2026-09-18.csv", "2026-09-18"),
    ("2026-09-18_positions_final.csv", "2026-09-18"),
    ("book_TEMPLATE_2026-09-18.parquet", "2026-09-18"),
])
def test_asof_is_read_from_the_filename(name, expect):
    assert asof_from_filename(name) == pd.Timestamp(expect)


@pytest.mark.parametrize("name", ["book.csv", "book_20260918.csv", "book_09-18-2026.csv",
                                  "book_2026-09-18_2026-09-19.csv", "book_2026-13-45.csv"])
def test_filename_without_exactly_one_real_iso_date_fails(name):
    with pytest.raises(BookError):
        asof_from_filename(name)


def test_tenor_is_measured_from_the_book_date(price_book, asof):
    d = price_book(pd.read_csv(io.StringIO("expiry,strike,type,quantity,premium\n2026-11-18,20,C,1,1.0\n")))
    assert d["tenor"].iloc[0] == (pd.Timestamp("2026-11-18") - pd.Timestamp(DATA_DATE)).days


# ---------------------------------------------------------------- new market data reaches pricing
def test_a_day_added_to_daily_inputs_reaches_the_pricing_curve(tmp_path, monkeypatch, params, vov_params):
    """The VIX curve is rebuilt from raw + daily_inputs on every pricing run.  It used to be read
    from data/processed/, which only run.py (a refit) rewrites, so a new day's settles never
    reached price.py and every book dated after the last calibration failed curve_date."""
    from vixshock import data_sources
    from vixshock.portfolio import price_portfolio
    from vixshock.shock import load_market

    base = load_market(DATA_DATE)
    from vixshock.shock import current_futures
    vx = current_futures()[0]                  # rebuilt from data/raw: works on a fresh clone
    last = vx[vx["date"] == DATA_DATE]
    new_day = "2026-09-21"
    (tmp_path / "vx_settlements.csv").write_text(
        "date,contract_expiry,settle,volume,open_interest\n"
        + "".join(f"{new_day},{e},{s + 0.5:.4f},1000,10000\n"
                  for e, s in zip(last["contract_expiry"], last["settle"])))
    (tmp_path / "vix_spot.csv").write_text(f"date,close\n{new_day},15.30\n")
    (tmp_path / "spx.csv").write_text(f"date,close\n{new_day},7610.25\n")
    monkeypatch.setattr(data_sources, "DAILY", tmp_path)

    m = load_market(new_day)
    assert m.gaps(need_spx=True) == []
    assert m.asof == pd.Timestamp(new_day) and m.spx == 7610.25
    # every settle +0.5; three days of roll-down moves where CM-60 sits between contracts,
    # so the constant-maturity point moves by about, not exactly, 0.5
    assert float(m.vix_curve(60)) == pytest.approx(float(base.vix_curve(60)) + 0.5, abs=0.15)
    d = price_portfolio(read_book(TEMPLATE), params, vov_params, m, warn=lambda s: None)
    assert d["tenor"].iloc[0] == (pd.Timestamp("2026-10-21") - pd.Timestamp(new_day)).days
    # the VIX forwards are the NEW day's settles, read straight off the added rows
    vix = d[(d.underlying == "VIX") & (d.scenario == d.scenario.iloc[0])]
    old = dict(zip(pd.to_datetime(last["contract_expiry"]), last["settle"]))
    assert len(vix) == 4
    for _, x in vix.iterrows():
        assert x["forward_source"] == "VX settle"
        assert x["forward"] == pytest.approx(old[pd.Timestamp(x["expiry"])] + 0.5, abs=1e-9)


# ---------------------------------------------------------------- the desk feed, as it actually arrives
FEED_HEADER = "expiry,strike,type,quantity,vol,premium,src_bid,src_ask,src_close,cusip,issuer_name"
FEED_ROWS = [
    "2026-10-21,20,C,100,,1.25,1.20,1.30,1.25,FEEDVIX01,VOLATILITY INDEX (VIX)",
    "2026-12-16,100,C,-50,,0.04,0.00,0.08,0.04,FEEDVIX02,VOLATILITY INDEX (VIX)",
    "2026-12-18,6900,P,20,,30.70,30.20,31.20,30.70,FEEDSPX01,S&P 500 INDEX",
]


def feed(tmp_path, header=FEED_HEADER, rows=FEED_ROWS, prefix=""):
    p = tmp_path / f"book_{DATA_DATE}.csv"
    p.write_bytes((prefix + "\r\n".join([header] + rows) + "\r\n").encode("utf-8"))
    return read_book(p)


def test_the_feeds_exact_layout_validates(tmp_path, asof):
    """The desk's 11 columns exactly -- no multiplier, forward, premium_source or lots."""
    out = check(asof, feed(tmp_path))
    assert list(out["underlying"]) == ["VIX", "VIX", "SPX"]
    assert (out["multiplier"] == 100).all()
    assert list(out["zero_bid"]) == [False, True, False]


@pytest.mark.parametrize("header", [FEED_HEADER.replace(",", ", "), FEED_HEADER.upper(),
                                    FEED_HEADER.replace("expiry", " Expiry ")])
def test_header_spacing_and_case_do_not_matter(tmp_path, asof, header):
    assert len(check(asof, feed(tmp_path, header=header))) == 3


def test_excel_byte_order_mark_does_not_hide_the_first_column(tmp_path, asof):
    assert len(check(asof, feed(tmp_path, prefix="﻿"))) == 3


@pytest.mark.parametrize("stamp", [" 00:00:00", "T00:00:00", " 00:00"])
def test_expiry_with_a_midnight_time_is_accepted(tmp_path, asof, stamp):
    rows = [r[:10] + stamp + r[10:] for r in FEED_ROWS]
    assert list(check(asof, feed(tmp_path, rows=rows))["expiry"].dt.day) == [21, 16, 18]


def test_expiry_with_any_other_time_is_rejected(tmp_path, asof):
    with pytest.raises(BookError, match="YYYY-MM-DD"):
        check(asof, feed(tmp_path, rows=[FEED_ROWS[0][:10] + " 16:15:00" + FEED_ROWS[0][10:]]))


def test_zero_vol_is_ignored_where_there_is_a_premium(tmp_path, asof):
    """The feed's vol column is unused when a premium exists; a 0 in it must not reject the book."""
    rows = [r.replace(",100,,1.25,", ",100,0,1.25,") for r in FEED_ROWS]
    out = check(asof, feed(tmp_path, rows=rows))
    assert out["vol"].isna().all()


def test_option_expiring_on_the_pricing_date_is_excluded_not_fatal(tmp_path, asof):
    """SPX has daily expiries: a book marked at the close will hold options that just settled."""
    msgs = []
    rows = FEED_ROWS + [f"{DATA_DATE},7600,P,5,,1.00,0.95,1.05,1.00,FEEDSPX0DTE,S&P 500 INDEX"]
    out = check(asof, feed(tmp_path, rows=rows), warn=msgs.append)
    assert "FEEDSPX0DTE" not in set(out["cusip"]) and len(out) == 3
    assert any("FEEDSPX0DTE" in m for m in msgs)


def test_book_where_everything_expires_today_is_rejected(tmp_path, asof):
    with pytest.raises(BookError, match="expires on the pricing date"):
        check(asof, feed(tmp_path, rows=[f"{DATA_DATE},7600,P,5,,1.00,0.95,1.05,1.00,Z,S&P 500 INDEX"]))


def test_option_that_expired_before_the_pricing_date_is_rejected(tmp_path, asof):
    with pytest.raises(BookError, match="BEFORE"):
        check(asof, feed(tmp_path, rows=FEED_ROWS + ["2026-09-17,7600,P,5,,1.00,0.95,1.05,1.00,OLD,S&P 500 INDEX"]))


def test_xsp_labelled_as_spx_is_rejected(tmp_path, price_book):
    """XSP is a tenth of SPX and ALSO has multiplier 100, so only the strike can give it away."""
    b = feed(tmp_path, rows=FEED_ROWS + ["2026-12-18,690,P,20,,3.07,3.00,3.14,3.07,XSP1,S&P 500 INDEX"])
    with pytest.raises(BookError, match="XSP"):
        price_book(b)


def test_implied_vol_reports_no_solution_above_its_search_range():
    from vixshock.pricing import implied_vol
    import math
    assert math.isnan(implied_vol(3000.0, 7700.0, 690.0, 0.25, 0.04, False))   # no vol <= 500% gives this
