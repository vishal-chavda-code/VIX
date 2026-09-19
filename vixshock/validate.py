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

Every one of those now raises with the offending row numbers.

Dates are parsed STRICTLY as YYYY-MM-DD.  `pd.to_datetime` without a format guesses,
and the guess can change between files -- or within one column, depending on which
other rows happen to disambiguate it.  A risk model must not infer what you meant.
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

DATE_FMT = "%Y-%m-%d"
REQUIRED = ("expiry", "strike", "type", "quantity")
MAX_PLAUSIBLE_STRIKE = 500.0      # VIX has never printed above 90
MAX_PLAUSIBLE_PREMIUM = 500.0


class BookError(ValueError):
    """Raised when a book file cannot be trusted.  The message names the rows."""


def _rows(idx) -> str:
    """Human-facing row numbers: CSV line numbers, counting the header as line 1."""
    return ", ".join(str(int(i) + 2) for i in idx[:10]) + (" ..." if len(idx) > 10 else "")


def parse_expiry(series: pd.Series) -> pd.Series:
    """Strict YYYY-MM-DD.  Anything else raises, rather than being guessed at."""
    s = series.astype(str).str.strip()
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


def validate_book(book: pd.DataFrame, asof: pd.Timestamp, days_forward: int = 0,
                  warn=None) -> pd.DataFrame:
    """Check a book and return it with `expiry` parsed and `type` normalised.

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

    # ---- expiry: strict, and must be in the future
    b["expiry"] = parse_expiry(b["expiry"])
    dte = (b["expiry"] - pd.Timestamp(asof)).dt.days
    expired = dte <= days_forward
    if expired.any():
        raise BookError(
            f"{int(expired.sum())} option(s) expire on or before the evaluation date "
            f"({pd.Timestamp(asof).date()}"
            f"{f' + {days_forward} days forward' if days_forward else ''}): "
            f"rows {_rows(b.index[expired])}")

    # A VIX option expires on a Wednesday.  Anything else is very likely a bad parse.
    not_wed = b["expiry"].dt.dayofweek != 2
    if not_wed.any():
        warn(f"{int(not_wed.sum())} expiry date(s) are not a Wednesday, which is unusual for "
             f"VIX options and often means a date parsed wrong: rows {_rows(b.index[not_wed])} "
             f"({', '.join(str(d.date()) for d in b.loc[not_wed, 'expiry'].unique()[:5])})")

    # ---- type
    t = b["type"].astype(str).str.strip().str.upper()
    ok = t.str.startswith("C") | t.str.startswith("P")
    if not ok.all():
        bad = b.index[~ok]
        raise BookError(f"`type` must start with C or P. Bad value(s) at rows {_rows(bad)}: "
                        f"{', '.join(repr(v) for v in b.loc[bad, 'type'].unique()[:5])}")
    b["type"] = t.str[0]

    # ---- numeric columns
    for col, required in (("strike", True), ("quantity", True),
                          ("premium", False), ("vol", False), ("forward", False)):
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

    # ---- strike
    bad = b["strike"] <= 0
    if bad.any():
        raise BookError(f"`strike` must be positive. Rows {_rows(b.index[bad])}: "
                        f"{list(b.loc[bad, 'strike'].unique()[:5])}")
    high = b["strike"] > MAX_PLAUSIBLE_STRIKE
    if high.any():
        warn(f"strike(s) above {MAX_PLAUSIBLE_STRIKE:.0f} at rows {_rows(b.index[high])} -- "
             f"VIX has never printed above 90, so check the units")

    # ---- quantity
    if (b["quantity"] == 0).any():
        warn(f"zero quantity at rows {_rows(b.index[b['quantity'] == 0])} -- these contribute nothing")
    frac = b["quantity"] != b["quantity"].round()
    if frac.any():
        warn(f"fractional quantity at rows {_rows(b.index[frac])}")

    # ---- premium
    if "premium" in b.columns:
        bad = b["premium"] < 0
        if bad.any():
            raise BookError(f"`premium` cannot be negative. Rows {_rows(b.index[bad])}: "
                            f"{list(b.loc[bad, 'premium'].unique()[:5])}")
        zero = b["premium"] == 0
        if zero.any():
            warn(f"premium is exactly 0 at rows {_rows(b.index[zero])} -- no implied vol can be "
                 f"backed out, so these fall back to the ATM curve. Leave the field BLANK instead.")
            b.loc[zero, "premium"] = np.nan
        high = b["premium"] > MAX_PLAUSIBLE_PREMIUM
        if high.any():
            warn(f"premium above {MAX_PLAUSIBLE_PREMIUM:.0f} at rows {_rows(b.index[high])} -- "
                 f"premiums are in VIX points, not currency. Check the units.")

    # ---- vol: catch the sentinel trap
    if "vol" in b.columns:
        bad = b["vol"] <= 0
        if bad.any():
            raise BookError(
                f"`vol` must be positive if supplied. Rows {_rows(b.index[bad])} have "
                f"{list(b.loc[bad, 'vol'].unique()[:5])}.\n"
                f"  A 0 is a VALUE, not a blank -- it would price the option at zero "
                f"volatility. Leave the field empty, or omit the column.")
        pct = b["vol"] > 10
        if pct.any():
            warn(f"vol above 10 at rows {_rows(b.index[pct])} -- vol is a DECIMAL "
                 f"(1.27 = 127%), so these look like percentages")

    # ---- forward
    if "forward" in b.columns:
        bad = b["forward"] <= 0
        if bad.any():
            raise BookError(f"`forward` must be positive. Rows {_rows(b.index[bad])}")

    # ---- duplicates
    key = ["expiry", "strike", "type"]
    dup = b.duplicated(key, keep=False)
    if dup.any():
        warn(f"{int(dup.sum())} row(s) share an expiry/strike/type with another row "
             f"({_rows(b.index[dup])}) -- intentional if these are separate lots, but "
             f"a duplicated file would double-count")

    return b
