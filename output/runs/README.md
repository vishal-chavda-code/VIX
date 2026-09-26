# output/runs/ — one folder per pricing run

Every `python price.py` writes a self-contained, dated folder here. Nothing is
overwritten, so any number that was ever reported can be traced back to the exact
calibration, market data and book that produced it.

```
output/runs/
  LATEST.txt                      run id of the most recent run + PASS/FAIL
  20260926_083012_price/
    report.txt                    the full human-readable run, same as stdout
    manifest.json                 machine-readable provenance — read this first
    positions.csv                 every position x every scenario, keyed by cusip
    pnl_by_scenario.csv           currency P&L per scenario: VIX, SPX, total; vol-fixed vs vol-shocked
    floor_report.csv              per scenario: which floors bound, by how much, and the P&L they set
    floors.csv                    every single floor binding: scenario, cusip, floor, raw, floored
    shocked_curves.csv            the VIX curve under each scenario
    book_input.csv                a copy of the book exactly as supplied
```

A run whose `curve_date` gate fails prices nothing and writes no folder: it stops with the
verdict on screen.

## Run id

```
<YYYYMMDD>_<HHMMSS>_price
```

Local time at the start of the run. Sorts chronologically. It is **not** the pricing date —
that is `asof` in the manifest, taken from the book's filename.

## manifest.json — the provenance record

The important file. It answers "what produced this number?" without opening anything else.

```json
{
  "schema_version": 2,
  "run_id": "20260926_083012_price",
  "run_type": "price (frozen calibration, no refit)",
  "asof": "2026-09-18",
  "asof_source": "book filename",
  "book_file": "input/book_2026-09-18.csv",
  "n_positions": 8,
  "excluded_expiring_on_pricing_date": [],
  "n_positions_by_underlying": { "VIX": 4, "SPX": 4 },
  "positions_priced_off_market_vol": 8,
  "vix_positions_on_curve_forward": 0,
  "zero_bid_positions": 1,
  "floor_bindings": 2,
  "spot_vix": 14.81,
  "spx_close": 7650.5,
  "market_data": { "dates": { "vix_futures_cm": "2026-09-18", "spot_vix": "2026-09-18",
                              "spx_close": "2026-09-18" } },
  "calibration": {
    "vix_response": {
      "fit_window": "2004-03-29 -> 2026-09-18", "n_obs": 112793,
      "method": "envelope", "quantile": 0.95, "method_up": "lsq", "quantile_up": null,
      "written": "2026-09-19 11:13",
      "params": { "beta_0": 197.51, "k": 0.7586, "lam": 0.209, "beta_up_0": 68.2641, "lam_up": 0.2179 }
    },
    "vol_of_vol": { "...": "same shape" },
    "vov_source": "auto"
  },
  "config": { "up_branch_fit": "envelope", "vix_floor": 9.0, "vol_floor_vix": 0.2,
              "vol_floor_spx": 0.05, "spx_vol_scale": 1.0, "spx_dividend_yield": 0.013 },
  "gates": { "calibration_fresh": true, "curve_date": true, "book_vols": true, "book_forwards": true },
  "all_gates_pass": true
}
```

Two runs of the same book that disagree can be diffed on `calibration.params`, `config` and
`market_data.dates` — those are the only things that can make the number move.

`calibration.vix_response.method_up` is the estimator the frozen up branch was actually
fitted with; `config.up_branch_fit` is what the next calibration will use. While they differ,
the report carries a warning.

## positions.csv

One row per position per scenario, `cusip` first. Columns worth knowing:

| column | meaning |
|---|---|
| `cusip`, `underlying` | the position and its product (VIX or SPX) |
| `scenario`, `spx_ret` | the SPX shock applied |
| `forward`, **`forward_source`** | `VX settle` (the listed future expiring with the option), `SPX close + carry`, `book` (an override), or `CM curve (fallback)` |
| `fwd_shocked_raw`, `fwd_shocked` | the shocked forward before and after `VIX_FLOOR` |
| `vix_floor_bound` | `VIX_FLOOR` set this forward |
| `vol`, **`vol_source`** | `from premium`, `book vol`, `ATM curve (fallback)` (VIX) or `VOL_FLOOR_SPX (fallback)` (SPX) |
| `vol_shock`, `vol_shocked_raw`, `vol_shocked` | the vol move, and the shocked vol before and after its floor |
| `vol_floor_bound` | `VOL_FLOOR_VIX` / `VOL_FLOOR_SPX` set this vol |
| `zero_bid` | the mark's bid was 0 — the least reliable kind |
| `beyond_calibrated_tenor` | tenor past the last calibrated one (150d): the shock is extrapolated |
| `price_vol_fixed` | re-priced with the forward shocked but vol held still |
| `price_vol_shocked` | re-priced with both shocked — **this is the real number** |
| `pnl_vol_fixed`, `pnl_vol_shocked` | (price − base price) × quantity × multiplier, **currency** |

## pnl_by_scenario.csv

The headline table. VIX subtotal, SPX subtotal and portfolio total, each vol-fixed and
vol-shocked. `vol_of_vol_effect` = total vol-shocked − total vol-fixed: what you would have
missed by shocking the forwards but holding every implied vol still.

## floor_report.csv and floors.csv

A floor is a constant, not the model. For each scenario, `floor_report.csv` gives how many
positions each floor bound, the largest lift, and **`floor_effect`** — the P&L with the
floors minus the P&L with them removed. Non-zero means a constant set part of that
scenario's number. `floors.csv` lists every individual binding.

## Gates

A run is only usable if `all_gates_pass` is true.

| gate | fails when | what to do |
|---|---|---|
| `curve_date` | VIX curve, spot VIX or (SPX books) SPX close not from the book's date | check the download ran (the report's `daily refresh` line), or put that date's rows in `data/daily_inputs/` |
| `calibration_fresh` | parameters older than `MAX_CALIBRATION_AGE_DAYS` (400) | run `python run.py` to recalibrate — a reviewed event |
| `book_vols` | a position had no vol from its premium and fell back (VIX: ATM curve; SPX: `VOL_FLOOR_SPX`) | add or fix the premium — see `input/INPUT_CONTRACT.md` |
| `book_forwards` | a VIX position had no listed future on its expiry date (a weekly) and no `forward` in the book, so fell back to the interpolated curve | put that expiry's VIX future in the book's `forward` column |

## Housekeeping

Nothing here is deleted automatically. Folders are small (tens of KB) apart from the
copied book. Keep whatever the retention policy requires; the run id and `manifest.json`
are what make an old run meaningful.

## Calibration runs

`python run.py` writes a `<run-id>_calibrate/` folder here with the report, the fitted
parameters, the plots and a manifest. Calibration folders are committed: they are the record
of which parameters were in force and when.

**`LATEST_CALIBRATION.txt` names the calibration of record, and `price.py` prices with that
folder's parameters — nothing else.** `run.py` repoints it only if every gate passed; a failed
calibration keeps its folder for review but never comes into force. Commit the new folder and
`LATEST_CALIBRATION.txt` together, so every machine that pulls the repo prices with the same
calibration. (`output/response_params.json` and `vov_params.json` are `run.py`'s gitignored
working copies. Pricing never reads them, so a machine's own stale copy cannot be used by
mistake.)

The calibration's age for the `calibration_fresh` gate comes from its manifest's `run_at`,
not from file timestamps, which a clone or copy resets.
