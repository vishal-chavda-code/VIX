# PORT CHECKLIST — run this on the work machine

**This is a checklist, not a reference. Tick each box. Do not skip to the end.**

If you are an AI assistant on the work machine: this file is self-contained. It
assumes no access to the conversation that produced it and no internet. Work
through it in order with the user. Do not assume a specific market-data vendor —
**ask the user what implied-volatility sources they have access to** and adapt.

Repo context: `Work/VIX` holds an SPX→VIX shock model that reprices a book of VIX
options under SPX scenarios. A review found the book contains far out-of-the-money
VIX calls the model effectively cannot see. Background: `learning/FINDINGS.md`.

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

### ☐ 1.1 — Inventory what you actually have

Before hunting, write down every source on this machine that could give an
implied vol or an option premium for a VIX option:

```
Source 1: ______________________  can it give per-strike IV? ____
Source 2: ______________________  can it give per-strike IV? ____
Source 3: ______________________  can it give per-strike IV? ____
```

What is needed, per position, is **either**:
- market implied volatility as a decimal (1.27 = 127%), **or**
- market premium per contract (from which vol is backed out in Phase 2)

Order of preference:
1. Per-strike, per-expiry IV directly from an option chain — best
2. Market premiums per position — equally good, one extra step
3. The desk's own risk-system marks — if the book is marked daily, that source
   has vols
4. `data/raw/bbg_vix_impvol.csv` in this repo — **at-the-money only.** NOT
   sufficient for far-OTM strikes; that is the problem being investigated. Useful
   solely as the ATM baseline to measure skew against.

### ☐ 1.2 — Pull vols or premiums for the flagged positions

Start with the positions flagged in 0.2. If pulling the whole book is hard,
**the flagged ones are what matter.**

```
Positions with IV or premium obtained:  ______ of ______
```

### ☐ 1.3 — Record the ATM baseline

From `data/raw/bbg_vix_impvol.csv` or report section 6, note the at-the-money
vol at each tenor. The skew is the difference between a position's real vol and
this baseline.

```
ATM vol 30d: ______  60d: ______  90d: ______
```

---

## PHASE 2 — Back out vols from premiums (skip if 1.2 gave IVs directly)

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

## PHASE 4 — Apply the fix (no model change required)

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

## PHASE 5 — Unrelated but worth doing: the freshness gate

`run.py` only downloads new data when `data/raw/vx/` is **empty**:

```python
elif not data_sources.VX_DIR.exists() or not any(data_sources.VX_DIR.glob("*.csv")):
    data_sources.download_everything()
```

A plain `python run.py` weeks later silently prices off stale curves and still
prints **ALL GATES PASS**. The only clue is the `as of` line in section 7.

### ☐ 5.1 — Check how stale the current data is

```
"as of" date in report section 7:  ______
Today:  ______
Business days stale:  ______
```

### ☐ 5.2 — Propose a gate

In `vixshock/diagnostics.py`, add a gate beside the existing ones — FAIL if the
latest CM date is more than N business days behind today. Follow the pattern of
`gates["cm_vs_spot"]` in `full_report()`. Suggested N = 3.

Matters more here than on the build machine, because `--refresh` needs internet
that may not exist.

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
