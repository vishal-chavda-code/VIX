# PORT CHECKLIST — run this on the work machine

**This is a checklist, not a reference. Tick each box. Do not skip to the end.**

If you are an AI assistant on the work machine: this file is self-contained. It
assumes no access to the conversation that produced it and no internet. Work
through it in order with the user. Do not assume a specific market-data vendor —
**ask the user what implied-volatility sources they have access to** and adapt.

Repo context: `Work/VIX` holds an SPX→VIX shock model that reprices a book of VIX
options under SPX scenarios. A review found the book contains far out-of-the-money
VIX calls the model effectively cannot see. Background: `learning/FINDINGS.md`.

**Much of what this checklist used to ask you to do by hand is now in the code.**
Current state:

| | |
|---|---|
| `python price.py --book <file>` | prices a book in ~1s against the frozen calibration |
| `python run.py` | recalibrates + full validation. **Yearly**, a reviewed event |
| `input/INPUT_CONTRACT.md` | the complete spec for the book file — give this to whoever builds the feed |
| `output/runs/<run-id>/` | one self-contained folder per pricing run, with a `manifest.json` recording exactly which calibration and market data produced the numbers |
| `book_vols` gate | the run **FAILS** if any position lacks its own premium/vol, instead of silently marking far-OTM calls near zero |
| premium → implied vol | done automatically; supply `premium`, leave `vol` blank |

So Phases 1–4 below are mostly about *getting the data*, not about wiring it in.

---

## PHASE 0 — Before anything else (needs no market data)

This phase requires **only the book's strikes and expiries.** Do it first. It
produces the headline number even if every other phase fails.

### ☐ 0.1 — Establish the model's maximum reachable forward

```
python run.py
```

Read report section 7, the "shocked CM VIX curve" table. The **−20% row** is the
highest forward any configured scenario produces.

Reference from the 2026-09-16 run — **recompute, do not reuse**:

| tenor | base | max at −20% |
|---|---|---|
| 30d | 18.45 | 52.8 |
| 60d | 19.12 | 47.2 |
| 90d | 19.35 | 42.4 |
| 120d | 20.01 | 38.8 |

Record today's numbers here:

```
30d max: ________   60d max: ________
90d max: ________   120d max: ________
```

### ☐ 0.2 — Count the positions the model cannot see

For every **call** in the book, compare its strike to the max reachable forward
at its tenor (interpolate between the four tenors above).

```
Calls struck ABOVE the -20% forward:  ______ positions
Total contracts:                      ______
Total notional:                       ______
Net long or short?                    ______
```

**These positions never go in the money in any modelled scenario.** They
contribute approximately nothing to the risk measure regardless of how they are
priced.

### ☐ 0.3 — Check the tenor concentration

The model's two documented validation misses (Feb 2018, Aug 2024) are at the
**30 and 60-day** points. At 90 and 120 days it barely misses at all.

```
Of the flagged calls, how many expire within 60 days?  ______
```

If most are short-dated, they sit where the model's coverage is weakest. Say so.

**PHASE 0 gives you a defensible finding with no market data at all.** If you get
no further, you still have something to present.

---

## PHASE 1 — Find implied vols

### ☐ 1.1 — PREMIUM IS THE PREFERRED INPUT, NOT VOL

**Read this before hunting. It reverses what you might assume.**

The book CSV accepts both a `vol` column and a `premium` column. **Prefer the
premium.** Reasons:

1. **The premium is observable. A vol is derived** — someone had to pick a
   forward, a day-count, a rate and a model to produce it. If their choices
   differ from this model's, the vol is inconsistent with the pricer consuming it.
2. **Backing the vol out of the premium guarantees consistency.** `implied_vol()`
   uses this model's own forward, day-count, rate and Black-76. Round-trips exactly.

**Measured cost of a convention mismatch** (90d 100-strike call, 4c premium):

| convention used to derive the vol | implied vol | vs correct |
|---|---|---|
| this model (forward = VIX future, 91/365) | **126.92%** | — |
| **if derived off SPOT VIX instead** | **133.80%** | **+6.87 pts** |
| if 252 business days | 126.75% | −0.17 pts |
| if r = 0 | 126.78% | −0.14 pts |
| if expiry off by 2 days | 128.34% | +1.42 pts |

Day-count and rate barely matter. **The forward is what matters** — and it is
exactly the thing most likely to differ, because many systems quote VIX option
vols against spot VIX, which is the wrong underlying for this product.

Feeding the spot-derived vol into this model gives a price of **0.0636** against
a market price of **0.0400** — a **59% mark error**, from a vol that is
"correct" under its own convention.

**⚠️ Do NOT populate both columns.** In `shock.py` the precedence is
`vol` → `premium` → ATM curve. **If `vol` is present, `premium` is ignored
entirely.** Populating both silently discards your authoritative premium.

### ☐ 1.2 — Inventory what you actually have

Write down every source on this machine that could give a **premium** or an
implied vol for a VIX option:

```
Source 1: ______________________  premium? ____  IV? ____
Source 2: ______________________  premium? ____  IV? ____
Source 3: ______________________  premium? ____  IV? ____
```

Order of preference:
1. **Market premium per position** — best. Exact, convention-free.
2. Per-strike, per-expiry IV from an option chain — acceptable, but carries the
   source's conventions.
3. The desk's own risk-system marks.
4. `data/bloomberg_historical/vix_option_implied_vol.csv` — **at-the-money only**, a frozen
   frozen one-time load. NOT sufficient for far-OTM strikes; that is the problem
   being investigated. Useful only as the ATM baseline to measure skew against.

### ☐ 1.3 — Pull premiums for the flagged positions

Start with the positions flagged in 0.2.

```
Positions with a premium obtained:  ______ of ______
```

### ☐ 1.4 — GET BOTH IF YOU CAN — as a cross-check, not an input

If you can also get your source's implied vol, pull it into a **separate column
or file** (not the book's `vol` column). Then compare it against the vol backed
out of the premium:

| gap | what it means |
|---|---|
| within ~1 vol point | your source shares this model's conventions — it can be trusted for positions where only a vol is available |
| **~5–7 points high** | **your source is quoting off spot VIX.** A finding in itself — tell the desk |
| wildly different | something else is wrong: stale mark, wrong expiry, wrong contract. Find out before relying on anything from it |

This costs nothing extra if you are pulling both anyway, and it tells you whether
the vol source is usable at all.

```
Median gap between source IV and premium-derived IV:  ______ vol points
Verdict on the vol source:  ______________________
```

### ☐ 1.5 — Record the ATM baseline

From `data/bloomberg_historical/vix_option_implied_vol.csv` or `run.py` report section 6, note the at-the-money
vol at each tenor. The skew is the difference between a position's real vol and
this baseline.

```
ATM vol 30d: ______  60d: ______  90d: ______
```

---

## PHASE 2 — Back out vols from premiums

(The pipeline now does this automatically when you supply a `premium` column —
see PHASE 4. Do it by hand here only if you want the numbers for the writeup.)

The repo ships the inverter. No new maths.

```python
import sys
sys.path.insert(0, r"<path to the VIX repo>")
from vixshock.pricing import implied_vol
import config

vol = implied_vol(
    price=0.04,        # market premium per contract
    F=19.37,           # the VIX FUTURE for this expiry - NOT spot VIX
    K=100.0,           # strike
    T=91/365,          # days to expiry / 365
    r=config.RISK_FREE_RATE,
    is_call=True,
)
# -> 1.2692, i.e. 126.92%
```

Verified round-trip: 0.0400 → 126.92% → 0.040000 exactly.

**Two traps:**
- `F` must be the **VIX future** for that expiry, not spot VIX. Use the book's
  own forward mark if present, else the CM curve from report section 7.
- Returns `nan` if the price is below intrinsic. If you see `nan`, check the
  forward and the option type before trusting the input.

### ☐ 2.1 — Vols backed out for all flagged positions

```
Positions with a vol now:  ______ of ______
```

### ☐ 2.2 — Record the skew

For each flagged position: `skew = its vol − the ATM vol at its tenor`.

```
Largest skew found:  ______ vol points
Typical skew on the flagged positions:  ______ vol points
```

Reference: one position reviewed off-machine showed a 4-cent premium implying
**127% vol against a 72.5% ATM** — a **54 vol-point** skew.

---

## PHASE 3 — Size the error

### ☐ 3.1 — The mark error today

Price each flagged position twice and difference:

```python
from vixshock.pricing import black76
p_model  = black76(F, K, T, atm_vol,    r, is_call)   # what the model uses
p_market = black76(F, K, T, market_vol, r, is_call)   # reality
error    = (p_market - p_model) * quantity
```

```
Total mark error across the book:  ______
Direction (does the model understate the liability?):  ______
```

For a **short** position a positive error means the model **understates what you
owe**. That is the dangerous direction.

### ☐ 3.2 — The P&L understatement per scenario

Run the book twice:

```
python run.py --book book_atm.csv       > out_atm.txt
python run.py --book book_withvol.csv   > out_market.txt
```

Diff the "book P&L by scenario" table in section 7 of each.

```
Understatement at -10%:  ______
Understatement at -20%:  ______
```

Reference (single 90d 100-strike call, short 1): model showed **−1.80** at −20%
versus **−5.28** skew-aware. Roughly one third of the true loss.

---

## PHASE 4 — Apply the fix (ALREADY IN THE CODE — just supply premiums)

`vixshock/shock.py` falls back to the ATM curve only when the book's `vol` column
is **missing or null**:

```python
if "vol" not in b or b["vol"].isna().any():
    b["vol"] = b.get("vol", ...).fillna(pd.Series(vov_curve(b["tenor"]), ...))
```

So adding a `vol` column with real market vols makes base marks **exact**, with
no code change.

Book CSV format: `expiry (YYYY-MM-DD), strike, type (C/P), quantity [, forward, vol]`

### ☐ 4.1 — Populate `vol` for EVERY position, or none

**Critical.** The code fills only the *missing* entries from the ATM curve. A
partially-filled column silently produces a book mixing market vols and ATM vols,
and **nothing in the report flags it.** Same for `forward`.

```
All positions have a vol?  ______
```

### ☐ 4.2 — Note the caveat before anyone else finds it

The vol-of-vol shock is an **additive parallel shift** — it adds the same number
of vol points regardless of starting vol, so it carries full skew through the
scenario. Real far-OTM call skew **flattens** as the forward rises toward the
strike. So shocked vols on those strikes are likely **overstated**.

Conservative for a short position, but state it rather than be caught by it.

### ☐ 4.3 — Understand what this does NOT fix

Phase 0's finding stands. Correct marks ≠ tail coverage. Those positions still
never go in the money in any modelled scenario.

---

## PHASE 5 — Data freshness (FIXED — verify only)

This was a real defect and it has been fixed. Both entry points now enforce a
`data_fresh` gate that **FAILS the run** when the curve is older than
`config.MAX_DATA_AGE_DAYS` (7 calendar days). It no longer passes silently.

`price.py` additionally enforces `calibration_fresh` — the fitted parameters must
be no older than `config.MAX_CALIBRATION_AGE_DAYS` (400 days), backing the yearly
recalibration policy.

### ☐ 5.1 — Verify the gate fires on this machine

```
python price.py --book input/book_<date>.csv
```

Read the MARKET DATA block and the VERDICT:

```
latest curve date ______   ______ days old   GATE data_fresh: ______
calibration is ______ days old               GATE calibration_fresh: ______
```

### ☐ 5.2 — If `data_fresh` FAILS

The machine has no fresh curve. Either:
- let the run pull it (needs internet to CBOE — no key or account), or
- drop today's rows into `data/daily_inputs/` — see the README in that folder.
  `vx_settlements.csv` needs **all** listed monthly contracts, not just the front:
  the 120-day curve point needs the 4th–5th month.

### ☐ 5.3 — If `calibration_fresh` FAILS

Run `python run.py` to recalibrate — but treat it as a reviewed event, not a
routine step. Keep the diagnostic report it produces. See README section 0b.

---

## PHASE 6 — Which way does the book lean?

A one-line question with a large consequence. From the flagged far-OTM calls in
Phase 0.2:

```
Net LONG or SHORT the far-OTM calls?  ______
```

- **Short** → the model **understates the liability**, at the base mark and in the
  shock. This is the dangerous direction and the Feb-2018 failure mode: the thing
  that hurts is the thing the model cannot see.
- **Long** → the model **undervalues tail protection you are paying for**. Conservative
  for P&L, but the hedge looks worthless in the risk report, and someone may cut it
  on that basis.

Either way the number is wrong; the sign tells you which conversation to have.

---

## Summary sheet — fill this in and take it to the meeting

```
MODEL COVERAGE
  Max reachable 30d forward:            ____________
  Calls struck above it:                ____ positions, ____ contracts
  Of those, expiring within 60 days:    ____
  Net long / short:                     ____________

MARK ERROR
  ATM vol the model assigns (30d):      ____________
  Actual market vol on flagged calls:   ____________
  Skew:                                 ____ vol points
  Total mark error:                     ____________

SCENARIO UNDERSTATEMENT
  At -10%:                              ____________
  At -20%:                              ____________

DATA
  Report "as of" date:                  ____________
  Business days stale:                  ____
```

---

## What to say, and what not to say

**Do not say "the model is a fail."** The curve shock validates well — it clears
every 2008 leg at ratios 1.8–2.8 and COVID at 1.2, with a median ratio of 1.67
across fourteen historical crises. Saying "fail" is wrong and will cost
credibility on the part that is right.

**Do say:**

> "Two issues, different severity. First, the pricing layer uses one at-the-money
> vol per tenor with no skew. Our far-OTM calls are marked at effectively zero
> against real market premiums — orders of magnitude at the base level, before any
> shock. That's fixable today with no model change: the book file already supports
> a per-position vol column and the code uses it when present.
>
> Second, and structurally: no scenario moves the 90-day forward above ~42, so
> calls struck above that are never in the money in any modelled state. They pay
> off in vol-market dislocations — the regime the model documents itself as
> underestimating, Feb 2018 at ratio 0.72 and Aug 2024 at 0.81.
>
> The curve shock itself validates well and I'm not challenging it. Puts are much
> less affected: VIX skew is mild on the downside and the model's floor at 9
> approximates the real one."

**If asked "why not just recalibrate to cover Feb 2018?"** — calibrating to the
strict 22-year maximum (`config.ENVELOPE_QUANTILE = 1.0`) does cover both misses,
but it raises the −20% shock from 34 to 41 points across every scenario. So it is
coverable by brute force, at the cost of distorting everything else to accommodate
a mechanism the model does not represent. The underlying issue is still
single-factor: SPX fell 8% and VIX moved 17 points, and no SPX-driven model
explains that.

**If asked what would properly fix it** — three options, ascending cost:
1. Per-position vols in the book file (fixes marks today, not coverage)
2. A skew surface: vol as a function of moneyness and tenor
3. A second risk factor: a vol-specific shock independent of SPX — this is what
   would actually generate the Feb-2018 state. A redesign, not a patch.
