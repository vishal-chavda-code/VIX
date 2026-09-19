# Daily inputs — local files, no network

`run.py` reads these three CSVs on top of the frozen history in `data/raw/`.
On a date that appears in both, the row here wins. Leave a file with only its
header line if you have nothing to add. No API, no account, no key is involved.

If `config.DAILY_REFRESH` is on and the machine has internet, the run first
pulls the same numbers itself from CBOE's public files (and SPX from Yahoo);
these files are then only needed for anything the pull could not get. If there
is no internet, these files are the only source of today's data, and the run
**fails** once the curve is older than `config.MAX_DATA_AGE_DAYS`.

## ⚠️ DATE FORMAT — `YYYY-MM-DD` in every file, every date column

**Read this before writing any of the files below.** It is the easiest way to get a
silently wrong answer.

Every date in every file here must be **`YYYY-MM-DD`** — e.g. `2026-11-18`. That means
both the `date` column *and* `contract_expiry` in `vx_settlements.csv`.

### Why

These columns are handed to pandas, which **guesses** the format. When both numbers are
12 or under it assumes US order, month first:

```
01/02/2027  ->  2 January 2027      (what pandas reads)
01/02/2027  ->  1 February 2027     (what a UK/European feed means)
```

For `contract_expiry` that is a **one-month error in the tenor** of every position priced
off that contract — the wrong point on the curve, the wrong shock, no warning.

The guess can even change *within one column*, depending on the other rows:

```
[01/02/2027, 02/13/2027]  ->  [2027-01-02, 2027-02-13]   month-first
[01/02/2027, 13/02/2027]  ->  [2027-01-02, NaT       ]   13 cannot be a month
```

Same first value, different result.

### Safe and unsafe

| format | result |
|---|---|
| `2026-11-18` | **correct — use this** |
| `18-Nov-2026` | parses, but do not rely on it |
| `11/18/2026`, `18/11/2026` | parse only because 18 cannot be a month |
| **`01/02/2027`** | **ambiguous — silently wrong half the time** |
| `20261118` | rejected |
| `46345` (Excel serial) | rejected |

### Check after writing

- Open the CSV in a **text editor**, not Excel — Excel re-renders dates in the machine's
  locale on save, so what you see there is not what is in the file.
- A VIX `contract_expiry` is always a **Wednesday**. If one is not, the parse is wrong.
- After a run, check the `as of` date and the `tenor` column in the output. A position you
  believe is ~60 days out showing 30 or 90 means a date parsed wrong.

---

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
