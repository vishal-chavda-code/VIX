# Architecture gaps — what this needs to become

Two structural requirements the current codebase does not meet. Both are about
**what kind of software this is**, not about whether the maths is right. The
maths is fine; the packaging is wrong for the intended use.

Written 2026-09-18. Companion to `FINDINGS.md` (which covers the far-OTM call
exposure) and `PORT_CHECKLIST.md` (the work-machine runbook).

---

## GAP 1 — This is a research script, not a pricing pipeline

### The requirement

> When a new portfolio arrives, pull fresh market data and price the book of VIX
> options against it. Repeatable, on demand, as often as needed.

### What it actually is today

A **one-time calibration study** that happens to have a repricing function bolted
on the end. Running `python run.py --book mybook.csv` does this:

1. Re-ingest 273 CFE contract files from disk (~47,000 rows)
2. Rebuild the entire constant-maturity curve history, 2004 → today
3. Rebuild the SPX join, 20 pooled window lengths, ~113,000 observations
4. **Re-fit the response function from scratch** (non-linear least squares)
5. Re-fit the vol-of-vol layer from scratch
6. Re-run 14 stress episodes
7. Regenerate three PNG plots
8. *Then* price your book — the only part you asked for

Steps 1–7 are calibration. They produce the same answer every time until new
history accumulates. They do not depend on the book at all.

### The cost, measured

| operation | time |
|---|---|
| repricing a book using saved parameters | **0.90 seconds** |
| `python run.py --book ...` (refits everything) | **several minutes** |

The parameters are already saved to `output/response_params.json` and
`output/vov_params.json`. `shock.py` already loads them via `load_params()`.
**The fast path exists — it just is not exposed.**

### Why this matters beyond speed

1. **No separation of calibration from application.** A risk model should
   calibrate on a schedule (monthly, quarterly, after a regime change) and price
   on demand. Here they are welded together, so every pricing run silently
   re-derives the model. If the data changes, your parameters change, and nothing
   tells you the number moved because the *calibration* moved rather than the
   *book*.

2. **No reproducibility.** Two runs a week apart against the same book can give
   different answers, with no record of why. There is no parameter versioning, no
   "this book was priced with calibration X."

3. **Fragile.** Any failure anywhere in steps 1–7 blocks pricing. Demonstrated
   live: a date-column bug in the vol-of-vol writer (see GAP 2) made repricing
   impossible until the full pipeline was repaired — even though the saved
   parameters were perfectly fine.

4. **Wrong failure mode for data.** `run.py` only downloads when `data/raw/vx/`
   is **empty**. So a pricing run weeks later silently uses stale curves and still
   prints ALL GATES PASS. For a research script that is survivable. For a pricing
   pipeline it is a defect.

### What it should look like

Two entry points instead of one:

```
calibrate.py     # runs steps 1-7, writes params + the diagnostic report,
                 # run on a schedule or when re-validating
                 # SLOW, that is fine

price.py         # loads saved params, refreshes market data, prices a book
                 # FAST, run as often as needed
                 # FAILS LOUDLY if params are missing, stale, or data is old
```

`price.py` needs:
- **A freshness gate.** Refuse to price if the curve data is more than N business
  days old. Do not warn — refuse. Suggested N = 3.
- **Calibration provenance stamped on every output.** Which parameter file, fitted
  when, over what window. `response_params.json` already carries `fit_start`,
  `fit_end` and `n_obs` — surface them in the output.
- **A market-data refresh step** that is explicit and reports what it pulled and
  how old it is.

This is a repackaging job, not a rewrite. Every piece already exists.

---

## GAP 2 — Bloomberg dependency must be one-time-load only

### The requirement

> A one-time historical load from Bloomberg is acceptable. An **ongoing**
> dependency is not. Routine runs must work with no Bloomberg access.

### Where the dependency actually lives

Good news: it is confined to **one layer** — the vol-of-vol data (implied vol of
VIX options). Everything else already runs on free sources:

| component | source | Bloomberg? |
|---|---|---|
| VIX futures curve | CBOE CFE files | no |
| SPX | Yahoo `^GSPC` | no |
| spot VIX | CBOE `VIX_History.csv` | no |
| VVIX | CBOE `VVIX_History.csv` | no |
| **vol-of-vol at 60/90/180d** | **Bloomberg** | **yes** |

Only `vixshock/bloomberg.py` and the `VOV_SOURCE = "bloomberg"` branch in
`volofvol.py` touch it.

### The alternative already exists

`config.VOV_SOURCE` accepts `"vvix"`, which uses only the free CBOE VVIX file.
Tested, and the cost is small:

| SPX | Bloomberg T30 | VVIX-only T30 | Bloomberg T90 | VVIX-only T90 |
|---|---|---|---|---|
| −20% | 85.3 | 79.9 | 59.3 | 53.5 |
| −10% | 62.8 | 58.4 | 43.7 | 39.1 |
| −5% | 39.3 | 36.3 | 27.3 | 24.3 |

Within about 7%, and slightly *less* conservative. The vol-of-vol layer is the
second-order term anyway — the forward move dominates book P&L.

**The methodological cost, which must be disclosed:** VVIX is 30-day only, so
there is no data on how vol-of-vol fades with tenor. The code borrows the VIX
response's tenor-fade parameter and labels the fit
`"(lam assumed = VIX response lam)"` (`volofvol.py`, `fit_vov`). An honest
assumption, disclosed in the output — but an assumption. State it.

**A genuine upside:** the VVIX fit uses **101,770 observations vs Bloomberg's
69,419**, because Bloomberg's 180-day series has a 2009–2013 gap that drops those
years entirely.

### 🐛 BUG: the Bloomberg-free path is broken

Setting `VOV_SOURCE = "vvix"` and running the pipeline **crashes**:

```
ValueError: Missing column provided to 'parse_dates': 'date'
```

**Cause.** The VVIX path writes the date column as `DATE` (uppercase, inherited
from the CBOE file via `load_cboe_index`), but `shock.py` reads
`parse_dates=["date"]` (lowercase). The Bloomberg path writes lowercase, so this
fires **only on the Bloomberg-free path** — the one that must work.

**Fix.** One line in `volofvol.py`, in `run()`, before the `to_csv`:

```python
wide.index.name = "date"
```

Verified. Alternatively make `shock.py` tolerant on read (`index_col=0`, then
coerce), but normalising on write is cleaner.

**Note:** because the VVIX path had written that file during testing, repricing
was impossible until it was repaired — even though the saved parameters were
fine. A concrete illustration of GAP 1's fragility.

### The policy question for Monday

Two readings of "Bloomberg-free", and they differ:

1. **No ongoing dependency, historical cache is fine.** Keep
   `data/raw/bbg_vix_impvol.csv` as a frozen one-time load, set
   `VOV_SOURCE = "bloomberg"` reading only from cache, never call the terminal.
   Keeps the richer multi-tenor data.
2. **No Bloomberg-derived data at all.** Delete the cache, go `VOV_SOURCE =
   "vvix"`, accept the single-tenor limitation and the borrowed tenor-fade
   assumption.

Both are defensible. Option 1 preserves the better calibration; option 2 is
cleaner if provenance matters. **Ask.**

Note that option 1 still needs the bug fixed, because any fresh calibration on a
machine without the cache falls back to VVIX.

---

## GAP 3 — Constraints for the air-gapped machine

Requirements that follow from the deployment environment:

- **No API keys of any kind.** No market-data key, no AI-assistant key. Everything must run
  from local files plus whatever market data the machine already provides.
- **No internet assumed.** `--refresh` calls out to CBOE and Yahoo. On the work
  machine that will fail. The pipeline must work from data already on disk, and
  say clearly how old it is rather than failing obscurely.
- **Spot VIX is available locally** (the user has a feed or a calculated value).
  This should be an isolated, swappable input — a single function or config entry
  that says where spot VIX comes from, so a local feed can be substituted for the
  CBOE download without touching anything else.

### Where spot VIX enters today

`vixshock/data_sources.py`:

```python
def load_cboe_index(name: str) -> pd.Series:
    """VIX_History.csv / VVIX_History.csv -> Series of daily closes."""
```

Called as `load_cboe_index("vix")` from `cm.py` and `shock.py`. **This is already
a single choke point** — swapping in a local feed means changing one function, or
better, adding a config entry:

```python
SPOT_VIX_SOURCE = "cboe_csv"   # | "local_feed" | "computed"
```

and dispatching inside `load_cboe_index`. Same pattern for SPX, which
`load_spx()` already reads from a plain `date,close` CSV — the README notes you
can drop your own file there, so SPX is effectively solved already.

---

## Priority

1. **Fix the `DATE`/`date` bug** — one line, without it Bloomberg-free does not
   run at all.
2. **Decide the Bloomberg policy** — cache-only vs VVIX-only. A question, not
   work.
3. **Split calibrate from price** — the real work, and what turns this from a
   study into a pipeline.
4. **Add the freshness gate** — small, high value, prevents silent staleness.
5. **Isolate the spot VIX source** — small, makes the work machine viable.

Items 1, 2, 4 and 5 are each under an hour. Item 3 is the substantial one, and it
is repackaging rather than new modelling.

---

# THE PROPOSAL — frozen calibration, fresh curve (added 2026-09-19)

The current code refits the model on every run, including when you only want to
price a book. That is the wrong shape. Here is the right one, and it is a
repackaging job — every piece already exists.

## The two kinds of data, which the code currently conflates

| | what it is for | changes | source quality needed |
|---|---|---|---|
| **CALIBRATION** | producing the ten fitted numbers | rarely — quarterly at most | must be defensible; reviewed once |
| **PRICING** | today's starting point | every day | must be current and trusted |

## What the pricer ACTUALLY needs (verified by tracing the imports)

`shock.py` — the thing that reprices a book — reads exactly four inputs:

| input | fresh or frozen |
|---|---|
| `load_cm()` — today's CM VIX futures curve | **FRESH** |
| `load_cboe_index("vix")` — spot VIX | **FRESH** |
| `vov_levels.csv` — vol-of-vol level | **FRESH** (or superseded by book premiums) |
| `load_params()` / `load_vov_params()` — the ten numbers | **FROZEN** |

**`shock.py` reads no SPX data at all.** The SPX move is a *scenario you choose*,
not an observation. SPX (`load_spx`) is called only by `join.py`, `stress.py` and
`volofvol.py` — all calibration modules.

**Consequence: Yahoo is not in the daily pricing path.** It supplies SPX, which is
calibration-only. Under this architecture Yahoo is touched solely during a
scheduled recalibration, where a qualified vendor source can be substituted once
and frozen. This answers the "I don't like Yahoo" objection without changing any
maths.

## Why daily refitting is not defensible

1. **Risk numbers drift for reasons unrelated to the book.** Refresh the data,
   refit, and the ten numbers shift. Two runs on the same book days apart give
   different answers.
2. **No attribution.** You cannot tell whether a number moved because the book
   changed or because the calibration did.
3. **No reproducibility.** Nothing records which calibration priced which book.
4. **It cannot be validated.** A model validator signs off on *a model*. If the
   model re-derives itself on every run, there is no fixed object to sign off on.
   This is the governance argument and it is the strongest one.

The standard pattern on any risk desk: **calibrate on a schedule with review and
sign-off, then apply frozen parameters until the next recalibration.**

## The target shape

```
calibrate.py        run quarterly, or after a regime break, WITH REVIEW
                    - needs the full history (SPX, VVIX, CM, contract files)
                    - uses a qualified SPX source, not Yahoo
                    - writes output/response_params.json + vov_params.json
                      STAMPED with fit window, n_obs, data vintage, a version id
                    - emits the full diagnostic report and the stress table
                    - SLOW. That is correct and expected.

price.py            run daily, or on demand per book
                    - refreshes ONLY the VIX futures curve + spot VIX
                    - loads the frozen parameters, never refits
                    - FAILS if the curve is stale beyond MAX_DATA_AGE_DAYS
                    - FAILS if no calibration file is present
                    - stamps every output with the calibration version used
                    - ~1 second
```

Measured today: repricing off saved params takes **0.90 seconds**; a full
`run.py` takes several minutes.

## What already exists and can be reused as-is

- `load_params()` / `load_vov_params()` — the frozen-parameter loaders
- `output/response_params.json` — already carries `fit_start`, `fit_end`, `n_obs`
- `shock.data_age_days()` and the `data_fresh` gate
- `data/daily_inputs/` — the no-network local input path
- The whole of `shock.py` — it is already the fast path

## What needs adding

1. Split `diagnostics.full_report()` so the calibration steps (1–6) and the
   pricing steps (7) can run independently.
2. A calibration version stamp — a hash or timestamp written into the params
   files and echoed on every priced output.
3. A gate on `price.py`: refuse to run if the calibration is older than some
   policy limit (for example 6 months), the mirror of the data-freshness gate.

## Open question for the quant

**How often should recalibration happen, and what triggers it?** Options:
scheduled (quarterly), triggered (when the post-2012 drift warning fires), or on
a regime break. The regime-instability finding — post-2012 beta_0 is 227 vs 192
full-sample — is the argument for *more* frequent recalibration, but each one
should be a reviewed event, not a silent side effect of pricing a book.
