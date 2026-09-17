"""Step 1 -- normalise CFE contract files into one long table.

Output columns: date, contract_expiry, settle, close, volume, open_interest,
days_to_expiry (calendar days), source ('archive' | 'modern').

Quirks handled here, and nowhere else:
  * Two date formats (MM/DD/YYYY archive, YYYY-MM-DD modern).
  * Some archive files carry a disclaimer line above the header.
  * Prices before 2007-03-26 are quoted x10 (VBI convention) -> divided by 10.
  * Rows with settle == 0 are pre-listing / non-trading placeholders -> dropped.
  * Archive files have no expiry in the filename: expiry = last trade date in
    the file, cross-checked against the exchange rule (+-3 days).
"""
from __future__ import annotations

import datetime as dt
import io
import logging
import re
from pathlib import Path

import numpy as np
import pandas as pd

from .data_sources import VX_DIR, MONTH_CODES, vix_expiry_rule, ROOT

log = logging.getLogger(__name__)
PROCESSED = ROOT / "data" / "processed"
RESCALE_DATE = pd.Timestamp("2007-03-26")   # first day quoted at VIX level, not VIX x 10

COLS = {"Trade Date": "date", "Futures": "contract", "Close": "close", "Settle": "settle",
        "Total Volume": "volume", "Open Interest": "open_interest"}


def _read_contract_file(path: Path) -> pd.DataFrame:
    text = path.read_text(encoding="utf-8-sig")
    lines = text.splitlines()
    hdr = next(i for i, l in enumerate(lines) if l.startswith("Trade Date"))
    # a few 2004-05 files have trailing commas on some rows
    body = [l.rstrip(",") for l in lines[hdr:] if l.strip()]
    df = pd.read_csv(io.StringIO("\n".join(body)))
    df = df.rename(columns=COLS)[list(COLS.values())]
    df["date"] = pd.to_datetime(df["date"], format="mixed")
    for c in ("close", "settle", "volume", "open_interest"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def _expiry_for(path: Path, df: pd.DataFrame) -> tuple[pd.Timestamp, str]:
    m = re.match(r"VX_(\d{4}-\d{2}-\d{2})\.csv", path.name)
    if m:
        return pd.Timestamp(m.group(1)), "modern"
    m = re.match(r"CFE_([FGHJKMNQUVXZ])(\d{2})_VX\.csv", path.name)
    if not m:
        raise ValueError(f"unrecognised filename {path.name}")
    month = MONTH_CODES.index(m.group(1)) + 1
    year = 2000 + int(m.group(2))
    last = df["date"].max()
    rule = pd.Timestamp(vix_expiry_rule(year, month))
    if abs((last - rule).days) > 3:
        log.warning("%s: last trade date %s is %d days from rule expiry %s",
                    path.name, last.date(), (last - rule).days, rule.date())
    return last, "archive"


def build_long_table(vx_dir: Path = VX_DIR) -> tuple[pd.DataFrame, dict]:
    frames, report = [], {"files": 0, "rows_raw": 0, "rows_zero_settle": 0, "rows_rescaled": 0}
    for path in sorted(vx_dir.glob("*.csv")):
        df = _read_contract_file(path)
        expiry, source = _expiry_for(path, df)
        report["files"] += 1
        report["rows_raw"] += len(df)

        zero = df["settle"] <= 0
        report["rows_zero_settle"] += int(zero.sum())
        df = df.loc[~zero].copy()

        old = df["date"] < RESCALE_DATE
        report["rows_rescaled"] += int(old.sum())
        df.loc[old, ["close", "settle"]] /= 10.0

        df["contract_expiry"] = expiry
        df["source"] = source
        frames.append(df)

    long = pd.concat(frames, ignore_index=True)
    long["days_to_expiry"] = (long["contract_expiry"] - long["date"]).dt.days
    long = long[long["days_to_expiry"] >= 0]
    dup = long.duplicated(["date", "contract_expiry"], keep="first")
    report["rows_duplicate"] = int(dup.sum())
    long = long.loc[~dup].sort_values(["date", "contract_expiry"]).reset_index(drop=True)

    # Sanity: after rescaling every settle must look like a VIX level.  The
    # handful that do not are exchange placeholders (e.g. 1.00 on listing day).
    bad = (long["settle"] < 5) | (long["settle"] > 120)
    report["rows_implausible_settle_dropped"] = int(bad.sum())
    if bad.any():
        log.warning("dropping implausible settles:\n%s",
                    long.loc[bad, ["date", "contract_expiry", "settle", "close"]].head(10).to_string(index=False))
    long = long.loc[~bad]
    long = flag_stale(long)
    report["rows_stale"] = int(long["stale"].sum())
    report["rows_final"] = int(len(long))
    report["date_range"] = (long["date"].min().date(), long["date"].max().date())
    return long[["date", "contract_expiry", "days_to_expiry", "settle", "close",
                 "volume", "open_interest", "stale", "source"]], report


def flag_stale(long: pd.DataFrame) -> pd.DataFrame:
    """A settle that did not move on a day the front month moved more than
    STALE_FRONT_MOVE points is almost certainly not a real price."""
    import config
    long = long.sort_values(["contract_expiry", "date"]).copy()
    long["dsettle"] = long.groupby("contract_expiry")["settle"].diff()
    front = (long[long["days_to_expiry"] > 0].sort_values("days_to_expiry")
             .groupby("date").first()["dsettle"].rename("front_move"))
    long = long.join(front, on="date")
    long["stale"] = (long["dsettle"] == 0) & (long["front_move"].abs() > config.STALE_FRONT_MOVE)
    return long.drop(columns=["dsettle", "front_move"]).sort_values(["date", "contract_expiry"]).reset_index(drop=True)


def contracts_per_date(long: pd.DataFrame) -> pd.DataFrame:
    """How many contracts were listed each day and how far out the curve reached."""
    g = long.groupby("date")
    return pd.DataFrame({"n_contracts": g.size(), "max_dte": g["days_to_expiry"].max()})


def run(save: bool = True) -> tuple[pd.DataFrame, dict]:
    long, report = build_long_table()
    if save:
        PROCESSED.mkdir(parents=True, exist_ok=True)
        long.to_csv(PROCESSED / "vx_long.csv", index=False)
    return long, report


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    long, report = run()
    print("\n".join(f"{k:>24}: {v}" for k, v in report.items()))
    cpd = contracts_per_date(long)
    print("\ncontracts listed per date, by year (median / min) and max reach in days:")
    yr = cpd.groupby(cpd.index.year).agg(n_med=("n_contracts", "median"), n_min=("n_contracts", "min"),
                                         reach_med=("max_dte", "median"), reach_min=("max_dte", "min"))
    print(yr.to_string())
    print("\nsample rows:")
    print(long.sample(8, random_state=1).sort_values("date").to_string(index=False))
