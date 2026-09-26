"""Raw inputs.

Two kinds, kept apart on purpose:

  data/raw/            the HISTORY, loaded once by bootstrap_history.py (needs internet;
                       build machine only) and then frozen.  Never touched by run.py.
  data/daily_inputs/   what the DAILY run reads on top of the history: three plain CSVs
                       maintained by you or your market-data feed.  No API, no network.
                       See data/daily_inputs/README.md for the formats.

run.py imports only the load_* functions below; the download_* functions are
bootstrap-only.

Sources
-------
VIX futures   CFE.  Two URL layouts:
                2004-2013  cdn.cboe.com/resources/futures/archive/volume-and-price/CFE_<M><YY>_VX.csv
                2013-now   cdn.cboe.com/data/us/futures/market_statistics/historical_data/VX/VX_<expiry>.csv
              The modern path also serves 2013 but those files are truncated
              with zero settles, so the archive is used through Dec-2013.
SPX           history: Yahoo (^GSPC) via yfinance, once, at bootstrap.  New days: CBOE's
              public SPX_History.csv, appended by refresh_daily (history never rewritten).
VIX spot      cdn.cboe.com VIX_History.csv  (validation of CM-30 only).
VVIX          cdn.cboe.com VVIX_History.csv (free 30-day vol-of-vol; used as the
              fallback when data/bloomberg_historical/ is absent).
"""
from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path

import pandas as pd
import requests

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
VX_DIR = RAW / "vx"

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120"}
MONTH_CODES = "FGHJKMNQUVXZ"  # Jan..Dec

ARCHIVE_URL = "https://cdn.cboe.com/resources/futures/archive/volume-and-price/CFE_{code}{yy:02d}_VX.csv"
MODERN_URL = "https://cdn.cboe.com/data/us/futures/market_statistics/historical_data/VX/VX_{expiry:%Y-%m-%d}.csv"
VIX_URL = "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"
VVIX_URL = "https://cdn.cboe.com/api/global/us_indices/daily_prices/VVIX_History.csv"
SPX_URL = "https://cdn.cboe.com/api/global/us_indices/daily_prices/SPX_History.csv"

ARCHIVE_LAST_YEAR = 2013   # archive layout used for contracts expiring <= this year


def _get(url: str, timeout: int = 30) -> requests.Response | None:
    """Return the response if it is a CFE contract file, else None.
    Some archive files carry a one-line disclaimer above the header."""
    r = requests.get(url, headers=UA, timeout=timeout)
    if r.status_code == 200 and b"Trade Date" in r.content[:2000]:
        return r
    return None


# ---------------------------------------------------------------- expiry rule
def third_friday(year: int, month: int) -> dt.date:
    d = dt.date(year, month, 15)
    while d.weekday() != 4:
        d += dt.timedelta(days=1)
    return d


def vix_expiry_rule(year: int, month: int) -> dt.date:
    """Wednesday 30 days before the 3rd Friday of the following month.

    If that Friday is a holiday (Good Friday), CFE uses the preceding business
    day, so the true expiry can be a day or two earlier.  Callers should probe
    neighbouring dates when the rule date 403s.
    """
    ny, nm = (year + 1, 1) if month == 12 else (year, month + 1)
    return third_friday(ny, nm) - dt.timedelta(days=30)


# ---------------------------------------------------------------- VIX futures
def download_vx_archive(first_year: int = 2004, last_year: int = ARCHIVE_LAST_YEAR,
                        force: bool = False) -> list[Path]:
    """2004-2013 contract files.  Filename convention kept: CFE_<M><YY>_VX.csv."""
    VX_DIR.mkdir(parents=True, exist_ok=True)
    out = []
    for year in range(first_year, last_year + 1):
        for mi, code in enumerate(MONTH_CODES, start=1):
            path = VX_DIR / f"CFE_{code}{year % 100:02d}_VX.csv"
            if path.exists() and not force:
                out.append(path)
                continue
            r = _get(ARCHIVE_URL.format(code=code, yy=year % 100))
            if r is None:
                log.info("archive: no file for %s %d", code, year)
                continue
            path.write_bytes(r.content)
            out.append(path)
    return out


def download_vx_modern(first_year: int = ARCHIVE_LAST_YEAR + 1, last_year: int | None = None,
                       force: bool = False) -> list[Path]:
    """2014+ contract files, keyed by expiry date.  Probes rule date then +-3 days."""
    VX_DIR.mkdir(parents=True, exist_ok=True)
    last_year = last_year or (dt.date.today().year + 1)
    out = []
    for year in range(first_year, last_year + 1):
        for month in range(1, 13):
            existing = sorted(VX_DIR.glob(f"VX_{year:04d}-{month:02d}-*.csv"))
            if existing and not force:
                out.extend(existing)
                continue
            rule = vix_expiry_rule(year, month)
            found = None
            for delta in (0, -1, -2, -3, 1, 2, 3):
                cand = rule + dt.timedelta(days=delta)
                if cand.weekday() >= 5:
                    continue
                r = _get(MODERN_URL.format(expiry=cand))
                if r is not None:
                    found = (cand, r)
                    break
            if found is None:
                log.info("modern: no file for %04d-%02d (rule date %s)", year, month, rule)
                continue
            cand, r = found
            if cand != rule:
                log.info("modern: %04d-%02d expiry %s differs from rule %s", year, month, cand, rule)
            path = VX_DIR / f"VX_{cand:%Y-%m-%d}.csv"
            path.write_bytes(r.content)
            out.append(path)
    return out


def download_all_vx(force: bool = False) -> list[Path]:
    return download_vx_archive(force=force) + download_vx_modern(force=force)


# ---------------------------------------------------------------- indices
def download_cboe_index(url: str, name: str, force: bool = False) -> Path:
    path = RAW / f"{name}.csv"
    if path.exists() and not force:
        return path
    r = requests.get(url, headers=UA, timeout=30)
    r.raise_for_status()
    path.write_bytes(r.content)
    return path


def download_spx(force: bool = False, start: str = "2003-01-01") -> Path:
    """SPX daily closes.  yfinance at build time; on the work machine drop a CSV
    with columns date,close at data/raw/spx.csv and this is skipped."""
    path = RAW / "spx.csv"
    if path.exists() and not force:
        return path
    import yfinance as yf  # build-machine only
    df = yf.download("^GSPC", start=start, auto_adjust=False, progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    out = df[["Close"]].rename(columns={"Close": "close"})
    out.index.name = "date"
    out.to_csv(path)
    return path


def append_spx_from_cboe() -> int:
    """New SPX closes from CBOE's public index file, APPENDED after the last date already in
    data/raw/spx.csv.  Returns the number of days added.

    The daily path uses this, not yfinance: same publisher as every other series, no scraper,
    and the file holds completed days only (Yahoo returns today's intraday price as a "close"
    during market hours).  Existing history is never rewritten: CBOE and the Yahoo-built
    history differ by up to ~8 points on 42 days in 2005-06, and a data refresh must not move
    the calibration's inputs."""
    import io
    path = RAW / "spx.csv"
    hist = pd.read_csv(path, parse_dates=["date"])
    r = requests.get(SPX_URL, headers=UA, timeout=30)
    r.raise_for_status()
    c = pd.read_csv(io.StringIO(r.text))
    c.columns = [x.strip().upper() for x in c.columns]
    c["date"] = pd.to_datetime(c["DATE"], format="%m/%d/%Y")
    new = c[c["date"] > hist["date"].max()].sort_values("date")
    if len(new):
        with open(path, "a", newline="") as f:
            for d, v in zip(new["date"], new["SPX"].astype(float)):
                f.write(f"{d:%Y-%m-%d},{v}\r\n")
    return int(len(new))


DAILY = ROOT / "data" / "daily_inputs"


def _daily_series(name: str) -> pd.Series | None:
    """date,close from data/daily_inputs/<name>.csv, or None if absent/empty."""
    path = DAILY / f"{name}.csv"
    if not path.exists():
        return None
    df = pd.read_csv(path)
    df.columns = [c.strip().lower() for c in df.columns]
    if df.empty or "date" not in df or "close" not in df:
        return None
    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date")["close"].astype(float).sort_index()


def _overlay(hist: pd.Series, daily: pd.Series | None) -> pd.Series:
    """History with daily rows appended; on a shared date the daily value wins."""
    if daily is None or daily.empty:
        return hist
    return pd.concat([hist[~hist.index.isin(daily.index)], daily]).sort_index()


def load_spx() -> pd.Series:
    hist = pd.read_csv(RAW / "spx.csv", parse_dates=["date"]).set_index("date")["close"].sort_index()
    return _overlay(hist, _daily_series("spx")).rename("close")


def load_cboe_index(name: str) -> pd.Series:
    """Spot VIX ('vix') or VVIX ('vvix'): history from the CBOE file, overlaid
    with data/daily_inputs/vix_spot.csv or vvix.csv when present."""
    df = pd.read_csv(RAW / f"{name}.csv")
    df.columns = [c.strip().upper() for c in df.columns]
    df["DATE"] = pd.to_datetime(df["DATE"])
    col = "CLOSE" if "CLOSE" in df.columns else [c for c in df.columns if c != "DATE"][0]
    hist = df.set_index("DATE")[col].astype(float).sort_index()
    hist.index.name = "date"
    daily = _daily_series("vix_spot" if name == "vix" else name)
    return _overlay(hist, daily).rename(name.lower())


def load_daily_vx() -> pd.DataFrame | None:
    """Daily VIX futures settlements from data/daily_inputs/vx_settlements.csv,
    normalised to the long-table columns, or None if absent/empty."""
    path = DAILY / "vx_settlements.csv"
    if not path.exists():
        return None
    df = pd.read_csv(path)
    df.columns = [c.strip().lower() for c in df.columns]
    need = {"date", "contract_expiry", "settle"}
    if df.empty or not need.issubset(df.columns):
        return None
    df["date"] = pd.to_datetime(df["date"])
    df["contract_expiry"] = pd.to_datetime(df["contract_expiry"])
    for c in ("settle", "volume", "open_interest", "close"):
        df[c] = pd.to_numeric(df[c], errors="coerce") if c in df else float("nan")
    df["close"] = df["close"].fillna(df["settle"])
    df["source"] = "daily_inputs"
    return df[["date", "contract_expiry", "settle", "close", "volume", "open_interest", "source"]]


def refresh_daily() -> dict:
    """Daily network pull (config.DAILY_REFRESH): every unexpired contract file (expired ones
    never change), newly listed contracts, spot VIX, VVIX and new SPX closes.  No key, no
    email, no account: plain HTTPS to cdn.cboe.com only.  Raises on any failure so the
    caller can fall back to data/daily_inputs/."""
    import datetime as _dt
    today = _dt.date.today()
    n = 0
    for path in sorted(VX_DIR.glob("VX_*.csv")):
        exp = _dt.date.fromisoformat(path.stem[3:])
        if exp >= today - _dt.timedelta(days=1):
            r = _get(MODERN_URL.format(expiry=exp))
            if r is None:
                raise RuntimeError(f"could not fetch {path.name}")
            path.write_bytes(r.content)
            n += 1
    download_vx_modern(first_year=today.year, force=False)      # newly listed contracts
    download_cboe_index(VIX_URL, "vix", force=True)
    download_cboe_index(VVIX_URL, "vvix", force=True)
    added = append_spx_from_cboe()
    return {"contract_files_refreshed": n, "spx_days_added": added,
            "latest_spx": str(load_spx().index.max().date()),
            "latest_vix": str(load_cboe_index("vix").index.max().date())}


def download_everything(force: bool = False) -> None:
    logging.getLogger().setLevel(logging.INFO)
    files = download_all_vx(force=force)
    log.info("VX contract files: %d", len(files))
    download_cboe_index(VIX_URL, "vix", force)
    download_cboe_index(VVIX_URL, "vvix", force)
    download_spx(force)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    download_everything()
