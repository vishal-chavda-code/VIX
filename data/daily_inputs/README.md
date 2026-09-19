# Daily inputs — local files, no network

`run.py` reads these three CSVs on top of the frozen history in `data/raw/`.
On a date that appears in both, the row here wins. Leave a file with only its
header line if you have nothing to add. No API, no account, no key is involved.

If `config.DAILY_REFRESH` is on and the machine has internet, the run first
pulls the same numbers itself from CBOE's public files (and SPX from Yahoo);
these files are then only needed for anything the pull could not get. If there
is no internet, these files are the only source of today's data, and the run
**fails** once the curve is older than `config.MAX_DATA_AGE_DAYS`.

## `vx_settlements.csv` — VIX futures daily settlements

One row per listed contract per day. All listed monthly contracts, not just the
front (the 120-day point needs the 4th–5th month).

```
date,contract_expiry,settle,volume,open_interest
2026-09-18,2026-10-21,17.95,180432,210110
2026-09-18,2026-11-18,18.80,60211,95120
2026-09-18,2026-12-16,19.20,22150,48230
2026-09-18,2027-01-20,19.85,9001,25012
2026-09-18,2027-02-17,20.30,3120,11004
```

`contract_expiry` is the contract's final settlement date (the Wednesday 30
days before the third Friday of the following month). `volume` and
`open_interest` may be left blank; blank is treated as unknown, not as thin.

## `spx.csv` — S&P 500 close

```
date,close
2026-09-18,7636.55
```

## `vix_spot.csv` — spot VIX close

```
date,close
2026-09-18,16.90
```

Spot VIX is used for two things: validating the 30-day curve point, and
anchoring the near end of the curve on the few days a month when the front
contract has more than 30 days left. If your desk computes its own VIX, put it
here.

## Not needed daily

VVIX / VIX-option implied vols: only used to *calibrate* the vol-of-vol layer.
Pricing uses each option's own `vol` or `premium` from the book file. If you
want the calibration to see new days, add `vvix.csv` (`date,close`) here.
