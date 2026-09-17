"""Bloomberg adapter (blpapi over the local terminal, localhost:8194).

Only one thing is pulled: the history of at-the-money implied volatility of
VIX options at fixed tenors, from the VIX Index's option surface fields.
Everything is cached to data/raw/bbg_*.csv so a run never needs the terminal
after the first pull; on a machine without Bloomberg, drop the CSV in place.

    VVIX Index PX_LAST              -> vov_30    CBOE 30-day vol-of-vol index (the standard measure)
    VIX Index  CALL_IMP_VOL_60D     -> vov_60    60-day ATM implied vol of VIX options
               3MO_CALL_IMP_VOL     -> vov_90    3-month
               6MO_CALL_IMP_VOL     -> vov_180   6-month (history starts ~2010)

Rejected after inspection (kept here so nobody re-tries them):
    *_IMPVOL_100.0%MNY_DF and CALL_IMP_VOL_30D on VIX Index swing 30-130 day to
    day: moneyness is measured against spot VIX while the options price off the
    future.  1M_/2M_*_50DELTA fields are per listed expiry, which with weekly
    options can be days away, so they are not fixed-tenor.
"""
from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path

import pandas as pd

from .data_sources import RAW

log = logging.getLogger(__name__)

VOV_FIELDS = {
    "CALL_IMP_VOL_60D": "vov_60",
    "3MO_CALL_IMP_VOL": "vov_90",
    "6MO_CALL_IMP_VOL": "vov_180",
}
VOV_TENORS = (30, 60, 90, 180)
CACHE = RAW / "bbg_vix_impvol.csv"


def reachable(host: str = "localhost", port: int = 8194, timeout: float = 1.0) -> bool:
    import socket
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _session(host="localhost", port=8194):
    import blpapi
    opts = blpapi.SessionOptions()
    opts.setServerHost(host)
    opts.setServerPort(port)
    s = blpapi.Session(opts)
    if not s.start():
        raise RuntimeError("blpapi session failed to start (is the terminal logged in?)")
    if not s.openService("//blp/refdata"):
        raise RuntimeError("could not open //blp/refdata")
    return s


def bdh(securities: list[str], fields: list[str], start: dt.date, end: dt.date | None = None,
        host="localhost", port=8194) -> pd.DataFrame:
    """HistoricalDataRequest -> long DataFrame [date, security, field, value]."""
    import blpapi
    end = end or dt.date.today()
    s = _session(host, port)
    try:
        svc = s.getService("//blp/refdata")
        req = svc.createRequest("HistoricalDataRequest")
        for sec in securities:
            req.getElement("securities").appendValue(sec)
        for f in fields:
            req.getElement("fields").appendValue(f)
        req.set("startDate", start.strftime("%Y%m%d"))
        req.set("endDate", end.strftime("%Y%m%d"))
        req.set("periodicitySelection", "DAILY")
        req.set("nonTradingDayFillOption", "NON_TRADING_WEEKDAYS")
        req.set("nonTradingDayFillMethod", "NIL_VALUE")
        s.sendRequest(req)
        rows = []
        while True:
            ev = s.nextEvent(30000)
            for msg in ev:
                if msg.hasElement("responseError"):
                    raise RuntimeError(str(msg.getElement("responseError")))
                if not msg.hasElement("securityData"):
                    continue
                sd = msg.getElement("securityData")
                sec = sd.getElementAsString("security")
                if sd.hasElement("securityError"):
                    log.error("%s: %s", sec, sd.getElement("securityError"))
                    continue
                for fe in sd.getElement("fieldExceptions").values():
                    log.warning("%s field exception: %s", sec, fe)
                for row in sd.getElement("fieldData").values():
                    d = row.getElementAsDatetime("date")
                    for f in fields:
                        if row.hasElement(f):
                            rows.append((pd.Timestamp(d), sec, f, row.getElementAsFloat(f)))
            if ev.eventType() == blpapi.Event.RESPONSE:
                break
    finally:
        s.stop()
    return pd.DataFrame(rows, columns=["date", "security", "field", "value"])


def download_vix_impvol(start: dt.date = dt.date(2006, 3, 1), force: bool = False) -> Path:
    """VIX option ATM implied vol by tenor + VVIX, cached to CSV."""
    if CACHE.exists() and not force:
        return CACHE
    long = bdh(["VIX Index"], list(VOV_FIELDS), start)
    wide = long.pivot(index="date", columns="field", values="value").rename(columns=VOV_FIELDS)
    vv = bdh(["VVIX Index"], ["PX_LAST"], start)
    wide["vov_30"] = vv.set_index("date")["value"]
    wide = wide[[f"vov_{T}" for T in VOV_TENORS]]
    wide = wide.sort_index()
    wide.index.name = "date"
    wide.to_csv(CACHE)
    return CACHE


def load_vix_impvol() -> pd.DataFrame:
    df = pd.read_csv(CACHE, parse_dates=["date"]).set_index("date").sort_index()
    return df


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "test":
        print(bdh(["VIX Index", "VVIX Index"], list(VOV_FIELDS) + ["PX_LAST"],
                  dt.date.today() - dt.timedelta(days=10)).pivot_table(index="date", columns=["security", "field"], values="value"))
    else:
        print("reachable:", reachable())
        p = download_vix_impvol()
        df = load_vix_impvol()
        print(df.describe().round(1).to_string())
        print(df.tail(3).round(2).to_string())
