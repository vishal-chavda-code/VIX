# output/runs/ — one folder per pricing run

Every `python price.py` writes a self-contained, dated folder here. Nothing is
overwritten, so any number that was ever reported can be traced back to the exact
calibration, market data and book that produced it.

```
output/runs/
  LATEST.txt                      run id of the most recent run + PASS/FAIL
  20260921_083012_price/
    report.txt                    the full human-readable run, same as stdout
    manifest.json                 machine-readable provenance — read this first
    positions.csv                 every position x every scenario, fully expanded
    pnl_by_scenario.csv           book P&L per scenario, vol-fixed vs vol-shocked
    shocked_curves.csv            the VIX curve under each scenario
    book_input.csv                a copy of the book exactly as supplied
```

## Run id

```
<YYYYMMDD>_<HHMMSS>_price
```

Local time at the start of the run. Sorts chronologically.

## manifest.json — the provenance record

The important file. It answers "what produced this number?" without opening anything else.

```json
{
  "run_id": "20260921_083012_price",
  "run_type": "price (frozen calibration, no refit)",
  "run_at": "2026-09-21 08:30:12",
  "asof": "2026-09-18",
  "book_file": "input/book_2026-09-21.csv",
  "n_positions": 6,
  "positions_priced_off_market": 6,
  "spot_vix": 14.81,
  "market_data": { "latest_curve_date": "2026-09-18", "age_days": 1, "max_age_days": 7 },
  "calibration": {
    "vix_response": {
      "fit_window": "2004-03-29 -> 2026-09-18",
      "n_obs": 112910,
      "method": "envelope", "quantile": 0.95,
      "written": "2026-09-19 06:21",
      "params": { "beta_0": 191.6567, "k": 0.8975, "lam": 0.2006,
                  "beta_up_0": 69.8342, "lam_up": 0.2319 }
    },
    "vol_of_vol": { "...": "same shape" },
    "vov_source": "auto"
  },
  "calibration_age_days": 2,
  "gates": { "calibration_fresh": true, "data_fresh": true, "book_vols": true },
  "all_gates_pass": true
}
```

Two runs of the same book that disagree can be diffed on `calibration.params` and
`market_data.latest_curve_date` — those are the only things that can make the number move.

## positions.csv

One row per position per scenario. Columns worth knowing:

| column | meaning |
|---|---|
| `scenario`, `spx_ret` | the SPX shock applied |
| `fwd`, `fwd_shocked` | the VIX future for this expiry, before and after |
| `vol`, `vol_shocked` | implied vol before and after |
| **`vol_source`** | `from premium`, `book vol`, or `ATM curve (fallback)` |
| `price_vol_fixed` | re-priced with the forward shocked but vol held still |
| `price_vol_shocked` | re-priced with both shocked — **this is the real number** |
| `pnl_vol_fixed`, `pnl_vol_shocked` | the above × quantity |

Check `vol_source`. Any `ATM curve (fallback)` means that position was priced off an
at-the-money vol, which marks far-out-of-the-money calls near zero. The `book_vols` gate
fails the run when this happens.

## pnl_by_scenario.csv

The headline table. `vol_of_vol_effect` is the difference between the two P&L columns —
what you would have missed by shocking the forward but holding implied vol still.

## Gates

A run is only usable if `all_gates_pass` is true.

| gate | fails when | what to do |
|---|---|---|
| `calibration_fresh` | parameters older than `MAX_CALIBRATION_AGE_DAYS` (400) | run `python run.py` to recalibrate — a reviewed event |
| `data_fresh` | curve older than `MAX_DATA_AGE_DAYS` (7) | refresh market data, or drop files in `data/daily_inputs/` |
| `book_vols` | any position had no premium or vol | add premiums to the book — see `input/INPUT_CONTRACT.md` |

## Housekeeping

Nothing here is deleted automatically. Folders are small (tens of KB) apart from the
copied book. Keep whatever the retention policy requires; the run id and `manifest.json`
are what make an old run meaningful.

## Calibration runs

`python run.py` writes the full diagnostic report to `output/report_<time>.txt` and the
fitted parameters to `output/response_params.json` / `vov_params.json`. Those are separate
from this folder: this folder holds *pricing* runs, which consume those parameters.
