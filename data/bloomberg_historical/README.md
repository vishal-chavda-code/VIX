# Bloomberg historical data — NOT a dependency

This folder holds **one file**, pulled **once** from a Bloomberg terminal on
2026-09-17 and frozen. Nothing in the pipeline calls Bloomberg. There is no
Bloomberg code in the project. If this folder is deleted, the model still runs:
the vol-of-vol layer falls back to CBOE's free VVIX index (30-day only) and
says so in the report.

## `vix_option_implied_vol.csv`

Daily at-the-money implied volatility of VIX options at fixed tenors, 2006-03-01
to 2026-09-16, in vol points (90 = 90%).

| column | Bloomberg source | meaning |
|---|---|---|
| `vov_30` | `VVIX Index`, `PX_LAST` | CBOE 30-day vol-of-vol index (identical to the free CBOE file, checked to 0.2 pts) |
| `vov_60` | `VIX Index`, `CALL_IMP_VOL_60D` | 60-day ATM implied vol |
| `vov_90` | `VIX Index`, `3MO_CALL_IMP_VOL` | 3-month ATM implied vol |
| `vov_180` | `VIX Index`, `6MO_CALL_IMP_VOL` | 6-month ATM implied vol (gap 2009–2013) |

Values below 40 are bad prints and are nulled on load (4 / 32 / 17 / 24 rows).

## What it is used for

Only the vol-of-vol calibration (`vixshock/volofvol.py`): how much VIX-option
implied vol rises when SPX falls, by tenor. With this file the tenor fade is
*measured* from four tenors. Without it (VVIX only) the tenor fade is *assumed*
equal to the VIX response's, and the report labels it as an assumption.
Shocks differ by ~7% between the two (VVIX-only is slightly lower).

## Fields that were tried and rejected (so nobody re-pulls them)

`*_IMPVOL_100.0%MNY_DF` and `CALL_IMP_VOL_30D` on `VIX Index` swing 30–130
day to day because moneyness is measured against spot VIX while the options
price off the future. `1M_/2M_*_50DELTA` fields are per listed expiry, which
with weekly options can be days away, so they are not fixed-tenor.

## Refreshing (optional, never required)

If someone with a terminal wants to extend the history: `bdh` on the securities
and fields above, write the same four columns with a lowercase `date` index,
replace this file. The pipeline reads whatever is here.
