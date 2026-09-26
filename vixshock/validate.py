"""Book-file validation.  Reject bad input loudly instead of pricing it silently.

The failure mode this exists to prevent is not a crash -- it is a run that looks clean
and reports a wrong number.  Before this module:

    strike = -20        priced to nan
    strike = 0          priced at 18.56
    type   = "X"        treated as a PUT
    type   = " C"       treated as a PUT   <- a stray space flips every call in the book
    premium = -1.0      fell back to the ATM curve without a word
    expiry = 01/02/2027 parsed as 2 January under pandas' US-first guess, or
                        1 February under a European feed -- a one-month tenor error

Every one of those raises with the offending row numbers.  The 2026-09-26 schema pass
added the product columns (issuer_name -> underlying, multiplier), the position key
(cusip) and the mark's provenance (premium_source, src_bid/src_ask/src_close).

Dates are parsed STRICTLY as YYYY-MM-DD -- in the expiry column and in the book
filename, which is where the pricing date comes from.  `pd.to_datetime` without a
format guesses, and the guess can change between files.  A risk model must not infer
what you meant.

Derived fields are computed here, never trusted from the feed:
    underlying  from issuer_name, via config.PRODUCTS.  Unrecognised names FAIL.
    zero_bid    src_bid == 0.  The least reliable marks.  Flagged, not fixed.
The one deliberate exception is `multiplier`: the feed supplies it and it must agree
with config.PRODUCTS, so a new product cannot silently price at the wrong size.
"""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pandas as pd

import config

DATE_FMT = "%Y-%m-%d"
REQUIRED = ("cusip", "issuer_name", "expiry", "strike", "type", "quantity",
            "premium", "src_bid", "src_ask", "src_close")
# Optional:
#   multiplier  taken from config.PRODUCTS for the issuer_name; if the book sends one it must match
#   forward     VIX: the day's settle of the future expiring with the option; SPX: close * carry
#   premium_source, vol, lots
# Which field fed `premium`.  "premium" = the feed's own premium field, as delivered, with no
# claim about which side of the market it is; it is the default when the column is absent
# or blank.  bid/ask/mid/close are claims, and are checked against the src_* columns.
PREMIUM_SOURCES = ("premium", "bid", "ask", "mid", "close")
MAX_PLAUSIBLE_STRIKE = {"VIX": 500.0, "SPX": 50_000.0}      # VIX has never printed above 90
MAX_PLAUSIBLE_PREMIUM = {"VIX": 500.0, "SPX": 20_000.0}
PREMIUM_SOURCE_TOL = 0.0051                                  # half a cent: a mid rounded to the tick
# Read these as text: a CUSIP that happens to be all digits loses its leading zeros as a number.
TEXT_COLUMNS = {"cusip": str, "issuer_name": str, "premium_source": str, "type": str}


class BookError(ValueError):
    """Raised when a book file cannot be trusted.  The message names the rows."""


def _rows(idx) -> str:
    """Human-facing row numbers: CSV line numbers, counting the header as line 1."""
    return ", ".join(str(int(i) + 2) for i in idx[:10]) + (" ..." if len(idx) > 10 else "")


def read_book(path) -> pd.DataFrame:
    """Read a book file.  Use this, not pd.read_csv.

    Every column is read as TEXT -- validate_book converts the numeric ones, strictly -- so
    nothing is type-guessed (an all-digit CUSIP keeps its leading zeros).  Column names are
    trimmed and lower-cased: "expiry, strike, type" or "Expiry" read the same as "expiry"."""
    path = Path(path)
    df = pd.read_parquet(path) if path.suffix.lower() == ".parquet" else \
        pd.read_csv(path, dtype=str, keep_default_na=True, encoding="utf-8-sig")
    df.columns = [str(c).strip().lower() for c in df.columns]
    return df


def asof_from_filename(path) -> pd.Timestamp:
    """The pricing date is the ONE YYYY-MM-DD date in the book's filename.

    Pricing runs as of this date -- tenors, the VIX curve, the SPX close -- never as of
    today().  A filename with no date, or more than one, is rejected rather than guessed."""
    name = Path(path).name
    found = re.findall(r"(?<!\d)(\d{4}-\d{2}-\d{2})(?!\d)", name)
    if len(found) != 1:
        raise BookError(
            f"the book filename must contain exactly one date as YYYY-MM-DD -- it is the "
            f"pricing date.  {name!r} has {len(found) or 'none'}"
            f"{': ' + ', '.join(found) if found else ''}.  e.g. book_2026-09-18.csv")
    d = pd.to_datetime(found[0], format=DATE_FMT, errors="coerce")
    if pd.isna(d):
        raise BookError(f"{found[0]!r} in the book filename is not a real date")
    return d


def parse_expiry(series: pd.Series) -> pd.Series:
    """Strict YYYY-MM-DD.  Anything else raises, rather than being guessed at.

    A midnight time suffix ("2026-11-18 00:00:00", "2026-11-18T00:00:00") is accepted and
    dropped: many exports write dates that way and it is not ambiguous.  Any other time is not."""
    s = series.astype(str).str.strip().str.replace(r"[ T]00:00(:00(\.0+)?)?$", "", regex=True)
    out = pd.to_datetime(s, format=DATE_FMT, errors="coerce")
    bad = out.isna()
    if bad.any():
        examples = ", ".join(repr(v) for v in s[bad].unique()[:5])
        raise BookError(
            f"expiry must be YYYY-MM-DD (e.g. 2026-11-18). "
            f"{int(bad.sum())} row(s) are not: rows {_rows(series.index[bad])}; values {examples}.\n"
            f"  Ambiguous forms like 01/02/2027 are rejected on purpose: that is 2 January "
            f"under a US convention and 1 February under a European one, and guessing wrong "
            f"shifts the tenor by a month with no warning."
        )
    return out


def _norm_text(s: pd.Series) -> pd.Series:
    """Upper-case, trimmed, internal whitespace collapsed; blanks become ''."""
    return s.fillna("").astype(str).str.strip().str.upper().str.replace(r"\s+", " ", regex=True)


def validate_book(book: pd.DataFrame, asof: pd.Timestamp, days_forward: int = 0,
                  warn=None) -> pd.DataFrame:
    """Check a book and return it parsed and normalised, with `underlying` and `zero_bid` added.

    Raises BookError on anything that would produce a wrong number.  Issues that are
    suspicious but not necessarily wrong are passed to `warn` (default: print).
    """
    warn = warn or (lambda m: print(f"  WARNING: {m}"))
    b = book.copy()

    if len(b) == 0:
        raise BookError("the book is empty -- no positions to price")

    missing = [c for c in REQUIRED if c not in b.columns]
    if missing:
        raise BookError(f"missing required column(s): {', '.join(missing)}. "
                        f"Required: {', '.join(REQUIRED)}. See input/INPUT_CONTRACT.md")

    # ---- lots: removed from the spec.  Always 1 in the feed; anything else would mean
    #      `quantity` is not the whole position, and ignoring it would understate the book.
    if "lots" in b.columns:
        lots = pd.to_numeric(b["lots"], errors="coerce")
        bad = lots.notna() & (lots != 1)
        if bad.any():
            raise BookError(f"`lots` is no longer part of the book spec and is expected to be 1. "
                            f"Rows {_rows(b.index[bad])} have {list(lots[bad].unique()[:5])} -- "
                            f"fold it into `quantity` so quantity is the full signed position")
        b = b.drop(columns="lots")

    # ---- cusip: the position key.  Every output row traces back to one of these.
    b["cusip"] = b["cusip"].fillna("").astype(str).str.strip()
    blank = b["cusip"] == ""
    if blank.any():
        raise BookError(f"`cusip` is the position key and is blank at rows {_rows(b.index[blank])}")
    dup = b["cusip"].duplicated(keep=False)
    if dup.any():
        raise BookError(f"`cusip` must be unique -- it is the position key.  Rows {_rows(b.index[dup])} "
                        f"repeat {list(b.loc[dup, 'cusip'].unique()[:5])}.  Aggregate each contract "
                        f"into one row with the net signed quantity")

    # ---- issuer_name -> underlying.  No default: an unknown product fails.
    known = {k.upper(): v for k, v in config.PRODUCTS.items()}
    iss = _norm_text(b["issuer_name"])
    unknown = ~iss.isin(known)
    if unknown.any():
        raise BookError(f"unrecognised `issuer_name` at rows {_rows(b.index[unknown])}: "
                        f"{', '.join(repr(v) for v in b.loc[unknown, 'issuer_name'].unique()[:5])}.  "
                        f"Known: {', '.join(repr(k) for k in config.PRODUCTS)}.  A new product must "
                        f"be added to config.PRODUCTS with its multiplier -- it is never defaulted")
    b["underlying"] = iss.map(lambda s: known[s]["underlying"])
    if "underlying" in book.columns:          # if the feed also sends one, it must agree
        given = _norm_text(book["underlying"])
        clash = (given != "") & (given != b["underlying"])
        if clash.any():
            raise BookError(f"`underlying` disagrees with `issuer_name` at rows {_rows(b.index[clash])}")
    is_vix = b["underlying"] == "VIX"

    # ---- expiry: strict.  An option that expired BEFORE the pricing date means a stale file:
    #      reject.  One expiring ON the pricing date has settled -- normal for SPX, which has
    #      daily expiries -- and carries no scenario risk: dropped, and listed by the caller.
    b["expiry"] = parse_expiry(b["expiry"])
    dte = (b["expiry"] - pd.Timestamp(asof)).dt.days
    stale = dte < 0
    if stale.any():
        raise BookError(
            f"{int(stale.sum())} option(s) expired BEFORE the pricing date {pd.Timestamp(asof).date()} -- "
            f"is this the right book for this date?  Rows {_rows(b.index[stale])}")
    today = dte == 0
    if today.any():
        warn(f"{int(today.sum())} option(s) expire ON the pricing date and are excluded (settled, no "
             f"scenario risk): {list(b.loc[today, 'cusip'])}")
        b, dte, iss, is_vix = b[~today], dte[~today], iss[~today], is_vix[~today]
        if len(b) == 0:
            raise BookError("every position expires on the pricing date -- nothing left to price")
    aged_out = dte <= days_forward
    if aged_out.any():
        raise BookError(
            f"{int(aged_out.sum())} option(s) expire within the {days_forward} days forward: "
            f"rows {_rows(b.index[aged_out])}")

    # A VIX option expires on a Wednesday.  Anything else is very likely a bad parse.
    not_wed = is_vix & (b["expiry"].dt.dayofweek != 2)
    if not_wed.any():
        warn(f"{int(not_wed.sum())} VIX expiry date(s) are not a Wednesday, which is unusual for "
             f"VIX options and often means a date parsed wrong: rows {_rows(b.index[not_wed])} "
             f"({', '.join(str(d.date()) for d in b.loc[not_wed, 'expiry'].unique()[:5])})")

    # ---- type
    t = b["type"].fillna("").astype(str).str.strip().str.upper()
    ok = t.str.startswith("C") | t.str.startswith("P")
    if not ok.all():
        bad = b.index[~ok]
        raise BookError(f"`type` must start with C or P. Bad value(s) at rows {_rows(bad)}: "
                        f"{', '.join(repr(v) for v in b.loc[bad, 'type'].unique()[:5])}")
    b["type"] = t.str[0]

    # ---- numeric columns
    for col in ("forward", "multiplier"):
        if col not in b.columns:
            b[col] = np.nan
    for col, required in (("strike", True), ("quantity", True), ("multiplier", False),
                          ("premium", False), ("vol", False), ("forward", False),
                          ("src_bid", False), ("src_ask", False), ("src_close", False)):
        if col not in b.columns:
            continue
        coerced = pd.to_numeric(b[col], errors="coerce")
        bad = coerced.isna() & b[col].notna() & (b[col].astype(str).str.strip() != "")
        if bad.any():
            raise BookError(f"`{col}` must be numeric. Rows {_rows(b.index[bad])}: "
                            f"{', '.join(repr(v) for v in b.loc[bad, col].unique()[:5])}")
        if required and coerced.isna().any():
            raise BookError(f"`{col}` is required but blank at rows {_rows(b.index[coerced.isna()])}")
        b[col] = coerced

    # ---- multiplier: from the product table.  If the book sends one, it must agree.  (The
    #      protection against a new product pricing at the wrong size is the issuer_name check
    #      above, not this column: XSP's multiplier is 100 too.  SPX strikes are sanity-checked
    #      against the forward in portfolio.py, which is what catches a mislabelled XSP.)
    expect = iss.map(lambda s: known[s]["multiplier"])
    b["multiplier"] = b["multiplier"].fillna(expect)
    bad = b["multiplier"] != expect
    if bad.any():
        raise BookError(f"`multiplier` does not match config.PRODUCTS at rows {_rows(b.index[bad])}: "
                        f"book has {list(b.loc[bad, 'multiplier'].unique()[:5])}, expected "
                        f"{list(expect[bad].unique()[:5])}.  A different contract size means a different "
                        f"product -- add it to config.PRODUCTS rather than overriding here")

    # ---- strike
    bad = b["strike"] <= 0
    if bad.any():
        raise BookError(f"`strike` must be positive. Rows {_rows(b.index[bad])}: "
                        f"{list(b.loc[bad, 'strike'].unique()[:5])}")
    high = b["strike"] > b["underlying"].map(MAX_PLAUSIBLE_STRIKE)
    if high.any():
        warn(f"implausibly high strike(s) at rows {_rows(b.index[high])} for their underlying -- "
             f"check the units")

    # ---- quantity
    if (b["quantity"] == 0).any():
        warn(f"zero quantity at rows {_rows(b.index[b['quantity'] == 0])} -- these contribute nothing")
    frac = b["quantity"] != b["quantity"].round()
    if frac.any():
        warn(f"fractional quantity at rows {_rows(b.index[frac])}")

    # ---- premium
    bad = b["premium"] < 0
    if bad.any():
        raise BookError(f"`premium` cannot be negative. Rows {_rows(b.index[bad])}: "
                        f"{list(b.loc[bad, 'premium'].unique()[:5])}")
    zero = b["premium"] == 0
    if zero.any():
        warn(f"premium is exactly 0 at rows {_rows(b.index[zero])} -- no implied vol can be "
             f"backed out, so these have no market vol. Leave the field BLANK instead.")
        b.loc[zero, "premium"] = np.nan
    high = b["premium"] > b["underlying"].map(MAX_PLAUSIBLE_PREMIUM)
    if high.any():
        warn(f"premium implausibly high at rows {_rows(b.index[high])} -- premiums are in index "
             f"points, not currency. Check the units.")

    # ---- premium_source: which field fed the premium.  Absent or blank = "premium" (as delivered).
    if "premium_source" not in b.columns:
        b["premium_source"] = ""
    src = b["premium_source"].fillna("").astype(str).str.strip().str.lower()
    has_prem = b["premium"].notna()
    src = src.where(src != "", "premium")
    bad = has_prem & ~src.isin(PREMIUM_SOURCES)
    if bad.any():
        raise BookError(f"`premium_source` must be one of {', '.join(PREMIUM_SOURCES)} wherever a premium "
                        f"is given.  Rows {_rows(b.index[bad])}: "
                        f"{', '.join(repr(v) for v in b.loc[bad, 'premium_source'].unique()[:5])}")
    b["premium_source"] = src.where(has_prem, "")
    # Cross-check the claim against the source columns, where they are populated.
    implied = pd.Series(np.nan, index=b.index)
    implied[src == "bid"] = b["src_bid"]
    implied[src == "ask"] = b["src_ask"]
    implied[src == "close"] = b["src_close"]
    implied[src == "mid"] = (b["src_bid"] + b["src_ask"]) / 2
    off = has_prem & implied.notna() & ((b["premium"] - implied).abs() > PREMIUM_SOURCE_TOL)
    if off.any():
        warn(f"premium does not match the `{'/'.join(src[off].unique()[:3])}` source column it claims "
             f"to come from at rows {_rows(b.index[off])} -- check premium_source")

    # ---- zero_bid: derived, flagged, never fixed
    b["zero_bid"] = (b["src_bid"] == 0).fillna(False).astype(bool)
    if b["src_bid"].isna().any():
        warn(f"`src_bid` blank at rows {_rows(b.index[b['src_bid'].isna()])} -- zero_bid cannot be "
             f"determined for these and is recorded as False")

    # ---- vol: no longer part of the spec.  A legacy file may still carry the column; it is used
    #      only where there is no premium.  Catch the sentinel trap.
    if "vol" in b.columns:
        # A zero/negative vol is a sentinel, not a vol.  Where there is a premium the vol is never
        # used, so it is ignored; where there is not, it would price at zero vol -- rejected.
        sentinel = b["vol"] <= 0
        bad = sentinel & b["premium"].isna()
        if bad.any():
            raise BookError(
                f"`vol` must be positive if supplied. Rows {_rows(b.index[bad])} have "
                f"{list(b.loc[bad, 'vol'].unique()[:5])} and no premium.\n"
                f"  A 0 is a VALUE, not a blank -- it would price the option at zero "
                f"volatility. Leave the field empty, or omit the column.")
        if sentinel.any():
            warn(f"`vol` <= 0 at rows {_rows(b.index[sentinel])} ignored -- those rows have a premium, "
                 f"which is what they are priced from")
            b.loc[sentinel, "vol"] = np.nan
        pct = b["vol"] > 10
        if pct.any():
            warn(f"vol above 10 at rows {_rows(b.index[pct])} -- vol is a DECIMAL "
                 f"(1.27 = 127%), so these look like percentages")
    else:
        b["vol"] = np.nan

    # ---- forward
    bad = b["forward"] <= 0
    if bad.any():
        raise BookError(f"`forward` must be positive. Rows {_rows(b.index[bad])}")

    # ---- the same contract under two keys would double-count
    key = ["underlying", "expiry", "strike", "type"]
    dup = b.duplicated(key, keep=False)
    if dup.any():
        warn(f"{int(dup.sum())} row(s) share an expiry/strike/type with another row "
             f"({_rows(b.index[dup])}) under different cusips -- a duplicated file would double-count")

    return b
