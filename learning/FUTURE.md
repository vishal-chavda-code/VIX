# FUTURE.md — work to be done on the air-gapped machine

**Read this if you are an AI assistant on the work machine, or the model owner
working alone.** This file is self-contained on purpose: it assumes no access to
the conversation that produced it, and no internet.

Context: the `Work/VIX` repo holds an SPX→VIX shock model that reprices a book of
VIX options under SPX scenarios. A review found that the book contains far
out-of-the-money VIX calls the model effectively cannot see. This file says what
to check and how.

Background reading in this repo: `learning/FINDINGS.md` has the full analysis with
numbers. `README.md` section 6 lists the model's own documented limitations.

---

## TASK 1 — Get implied vols for the book's options (the blocker)

**Everything else depends on this.** Without per-position implied vols, the
mark error cannot be quantified.

### What is needed

For each option in the book: the **market implied volatility**, as a decimal
(e.g. 1.27 for 127%), or failing that the **market premium** so vol can be
backed out.

### Where to look, in order of preference

1. **Bloomberg terminal.** The book's options are VIX options. On a VIX option
   chain, the implied vol column is the direct answer. `OMON` on `VIX Index`
   gives the option monitor with IVs per strike and expiry.
2. **The desk's own risk system.** If the book is marked daily, the marks came
   from somewhere and that source has vols.
3. **Market premiums.** If only prices are available, back out the vol with the
   model's own function — see TASK 2.
4. **The existing Bloomberg cache.** `data/raw/bbg_vix_impvol.csv` already holds
   *at-the-money* vols by tenor (30/60/90/180d). These are NOT sufficient for
   far-OTM strikes — using them is exactly the problem being investigated — but
   they establish the ATM baseline to measure skew against.

### Success criterion

A CSV with one row per book position and a `vol` column (decimal) or a
`premium` column. That is the whole deliverable for this task.

---

## TASK 2 — Back out implied vols from premiums (if only prices are available)

The repo already ships the inverter. No new maths required.

```python
import sys
sys.path.insert(0, r"<path to the VIX repo>")
from vixshock.pricing import implied_vol
from vixshock.cm import load_cm
import config

# forward = the VIX FUTURE for that option's expiry, not spot VIX.
# Take it from the book's own mark if present, else the CM curve:
cm = load_cm()
# cm has columns cm_30, cm_60, cm_90, cm_120 indexed by date.
# Interpolate to the option's days-to-expiry, or use the book's forward mark.

vol = implied_vol(
    price=0.04,        # market premium per contract
    F=19.37,           # the VIX future for this expiry
    K=100.0,           # strike
    T=91/365,          # days to expiry / 365
    r=config.RISK_FREE_RATE,
    is_call=True,
)
# returns a decimal, e.g. 1.2692 == 126.92%
```

Verified round-trip: premium 0.0400 → vol 126.92% → re-priced 0.040000. Exact.

**Watch out:** `implied_vol` returns `nan` if the price is below intrinsic value
(a no-arbitrage violation). If you get `nan`, check the forward and the option
type before trusting the input price.

---

## TASK 3 — The exposure check (the number to walk into a meeting with)

Once vols exist, answer these five questions. They are the deliverable.

### 3a. How far out of the money is each position?

Compute `strike / forward` for every position, where forward is the VIX future
for that option's expiry. Anything above ~2.0x is in the danger zone; above 3.0x
the model marks it at essentially zero.

### 3b. Which positions are outside the model's reachable range?

Compute the highest forward any scenario produces, per tenor:

```python
from vixshock.response import load_params
P = load_params()
# For a position with `tenor` days to expiry and base forward F:
worst_forward = F + float(P.dvix(-0.20, tenor))   # -20% is the grid's worst
```

Reference values from the 2026-09-16 run (recompute with current marks):

| tenor | base | at −20% | at −30% |
|---|---|---|---|
| 30d | 18.45 | 52.8 | 72.4 |
| 60d | 19.12 | 47.2 | 63.3 |
| 90d | 19.35 | 42.4 | 55.5 |
| 120d | 20.01 | 38.8 | 49.6 |

**Any call struck above the −20% column never goes in the money in any modelled
scenario.** Flag those positions and total their notional. That number is the
headline.

### 3c. What is the mark error today?

For each position, price it twice — once with the model's ATM vol, once with the
market vol — and difference them:

```python
from vixshock.pricing import black76
p_model  = black76(F, K, T, atm_vol,    r, is_call)
p_market = black76(F, K, T, market_vol, r, is_call)
error = (p_market - p_model) * quantity
```

Sum across the book. For a short position, a positive error means the model
**understates the liability**.

### 3d. What is the P&L understatement per scenario?

Run the book twice through the model: once as-is (ATM vols), once with a `vol`
column populated from market vols. Compare the P&L summaries.

```
python run.py --book book_atm.csv     > out_atm.txt
python run.py --book book_withvol.csv > out_market.txt
```

Diff the "book P&L by scenario" tables in section 7 of each report.

**Reference figure from the review** (90d 100-strike call, short 1 contract):
at SPX −20% the model showed −1.80 versus −5.28 skew-aware. Roughly one third of
the true loss. Recompute with real marks.

### 3e. Which way does the book lean?

Net long or short the far-OTM calls?

- **Short** → the model understates the liability. Dangerous. This is the
  Feb-2018 failure mode: the thing that hurts is the thing the model cannot see.
- **Long** → the model undervalues tail protection being paid for, so a hedge
  looks worthless in the risk report and someone might cut it.

---

## TASK 4 — The fix that needs no model change

`vixshock/shock.py` only falls back to the at-the-money curve when the book's
`vol` column is **missing or null**:

```python
if "vol" not in b or b["vol"].isna().any():
    b["vol"] = b.get("vol", ...).fillna(pd.Series(vov_curve(b["tenor"]), ...))
```

So: **add a `vol` column to the book CSV with real market implied vols, and base
marks become exact.** No code change. Same applies to a `forward` column.

Book CSV format (from `shock.py` docstring):
```
expiry (YYYY-MM-DD), strike, type (C/P), quantity  [, forward, vol]
```

### Three warnings

1. **Populate `vol` for EVERY position or none.** The code fills only the
   *missing* entries from the ATM curve. A partially-filled column silently
   produces a book mixing market vols and ATM vols, and nothing in the report
   flags it.
2. **The vol-of-vol shock is an additive parallel shift.** It adds the same
   number of vol points regardless of the starting vol, so it carries full skew
   through the scenario. Real far-OTM call skew *flattens* as the forward rises
   toward the strike, so shocked vols on those strikes are likely overstated.
   Conservative for a short position, but say so before someone else finds it.
3. **This fixes marks, not coverage.** TASK 3b's finding is unaffected — those
   positions still never go in the money in any modelled scenario.

---

## TASK 5 — Add a data-freshness gate (separate, unrelated to the book)

`run.py` only downloads new data when `data/raw/vx/` is **empty**:

```python
elif not data_sources.VX_DIR.exists() or not any(data_sources.VX_DIR.glob("*.csv")):
    data_sources.download_everything()
```

So a plain `python run.py` weeks later silently prices off stale curves and still
prints **ALL GATES PASS**. The only clue is the `as of` line in report section 7.

**Proposed fix:** in `vixshock/diagnostics.py`, add a gate alongside the existing
ones — FAIL if the latest CM date is more than N business days behind today.
Follow the pattern of `gates["cm_vs_spot"]` in `full_report()`. Suggested N = 3.

This matters more on the work machine than the build machine, because `--refresh`
needs internet that may not be there.

---

## Priority

1. **TASK 1** — get vols. Everything is blocked on this.
2. **TASK 3b** — which positions are outside the reachable range. This is the
   headline finding and needs no vols, only strikes and the table above.
3. **TASK 3c/3d** — size the mark error and the P&L understatement.
4. **TASK 4** — apply the fix.
5. **TASK 5** — freshness gate. Unrelated but worth doing.

Note that **TASK 3b can be done right now with no vols at all** — it only needs
the book's strikes and expiries. If time is short, do that first.
