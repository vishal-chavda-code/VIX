# Data request: historical SPX vol surfaces

**Written for the quant / surface-building team. Hand this over directly.**

## What we are building

A scenario shock model that answers: *if SPX moves X%, what is a book of SPX and VIX
options worth?* An equivalent model for VIX already exists, is calibrated on 22 years of
data and validated against 14 historical crises. We are extending it to SPX options.

Pricing SPX options under a scenario needs two things to move:

1. **The forward** — trivial, the scenario *is* the move.
2. **The vol surface** — not trivial. An SPX drop does not just lower the index; it lifts
   the whole surface and steepens the skew. Holding the surface fixed would materially
   understate long-put P&L.

**We want to measure (2) empirically rather than assume it.**

---

## The ask, in one line

**A daily history of SPX implied vol surfaces, as far back as you can reasonably go.**

### Preferred form

One row per (date, tenor, moneyness), with vol in decimals:

```
date,tenor_days,moneyness,implied_vol
2020-03-16,30,0.80,0.9120
2020-03-16,30,0.90,0.7845
2020-03-16,30,1.00,0.6930
2020-03-16,30,1.10,0.6204
...
```

- **tenor_days** — constant maturity if you have it (30/60/90/120/150/180); listed expiries
  are fine too, we can interpolate
- **moneyness** — strike / forward is ideal. Strike / spot or delta also work, we just need
  to know which
- **implied_vol** — decimal (0.20 = 20%)

Any equivalent shape works — raw parameters (SVI, SABR) plus an evaluator, or a full grid.
Tell us the convention and we adapt.

### Also needed, and easy to forget

- **the forward per (date, expiry)** — we believe the feed may not publish this. If it
  does not, a near-the-money **call and put price at the same strike** per expiry lets us
  imply it from put-call parity, which avoids needing a dividend forecast at all
- the **rate** convention used
- whether vols are **mid**, bid, or ask

---

## How much history — and why a few event dates is not enough

Our first instinct was to request a handful of dates around known selloffs: the day
before, the day after. **That turns out to be too thin**, and the reason is worth stating.

We need to fit how the surface shifts as a *function* of the SPX move — a level shift, a
slope change, and how both vary by tenor. Several parameters. Fitting those from a dozen
event dates would repeat a mistake the VIX model already documents: fitted on any single
short window, the response either explodes or collapses, because the tail is too sparse.

Counting SPX windows of 1–20 trading days (the pooling the VIX model uses):

| history supplied | windows below −5% | below −10% | below −20% |
|---|---|---|---|
| 3 years | 348 | — | — |
| 5 years | 1,288 | — | — |
| **10 years** | **2,265** | **408** | **76** |
| full (22y) | 5,223 | — | — |

**10 years is the target.** It captures Feb 2018, Q4 2018, COVID 2020, 2022, Aug 2024 and
April 2025 — enough distinct regimes to separate a genuine relationship from one crisis.

**5 years is workable** but leans heavily on 2020 and 2022.

**Less than 3 years is not useful** for the downside, which is the half that matters.

If a long history is expensive, a reasonable compromise: **full daily history for the last
3 years, plus daily coverage of a window around each major selloff** — say 60 days either
side of Feb-2018, Q4-2018, Feb–Apr 2020, Jan–Oct 2022, Aug-2024, Apr-2025. That preserves
the tail observations, which are the ones that set the calibration.

---

## Why daily, not just event dates

Three reasons:

1. **The shock is defined over a window, not a day.** "SPX falls 20%" can happen over 3
   days or 20. The VIX model pools every window length 1–20 days and calibrates to the
   worst response seen for a move of that size, however fast. That needs continuous daily
   data, not snapshots.
2. **We reuse validated machinery.** The same pooled-window envelope fit is already built,
   tested and validated for VIX. A continuous surface history lets us extend a reviewed
   method rather than invent a new one.
3. **Calm days matter too.** They set the baseline the shocked surface is measured against
   and tell us how much of the move is SPX-driven versus noise.

---

## The two questions we would most like your view on

Beyond the data itself:

1. **How should the surface move under a shock?** We can measure it, but you build these
   surfaces and will have priors we do not. Is a parallel level shift plus a slope term
   adequate? Does the shift scale with the level, with the move, with tenor?
2. **Given what data is actually available, which model should we use?** We would rather
   let the data drive the choice than arrive with a design and ask you to fill it.

---

## What we will do with it

- fit how the surface level and slope respond to SPX moves, by tenor
- calibrate conservatively — the existing VIX model fits the 95th percentile of observed
  responses rather than the average, so roughly 94.6% of historical selloffs produced a
  move at or below what it predicts
- validate against the same historical crises the VIX model is checked on
- report joint SPX + VIX book P&L under one scenario grid

## One caveat we will state in the output

Combining both books gives **scenario-consistent P&L, not a correlation model.** At
SPX −20% we assert the index falls 20% *and* the VIX front rises ~34 points, together,
with no dispersion. That is a single-factor assumption. It becomes load-bearing if the two
books are meant to hedge each other — Feb 2018 is the counter-example, SPX −8% with VIX
+17.5, which the VIX model underestimates at a ratio of 0.72.

---

## Why this is not simply public — and why it may still cost nothing

Worth understanding before treating this as a procurement request.

### Public

- **The concept and qualitative shape.** Skew is textbook. That SPX puts trade above ATM,
  that VIX calls do, that skew steepens in a selloff — none of it is proprietary.
- **Today's raw chains.** Delayed quotes are widely available; CBOE publishes a fair amount.
- **Aggregate indices.** VIX, VVIX and **SKEW** (CBOE's own SPX tail-skew index) are free
  and daily, SKEW back to 1990.
- **Academic datasets.** OptionMetrics IvyDB is the research standard — just not free
  commercially.

### What is actually being paid for

1. **History.** Today's chain is cheap; ten years of *every* chain, captured daily,
   stored and error-corrected, is a curated dataset. The product is warehousing, not
   insight.
2. **Cleaning — the underrated part.** Raw quotes are messy: stale marks on illiquid
   strikes, crossed bid/ask, prices violating put-call parity, wide spreads where nothing
   traded. Turning that into a usable surface means filtering and interpolating with a
   defensible choice at every step. **The cleaning is the product.**
3. **The surface fit.** Scattered strikes to a smooth, arbitrage-free surface needs a
   model (SVI, SABR) and calibration.

### Why free VIX futures but not free vol surfaces

CBOE publishes VIX futures settlements free because they are **exchange settlement
prices** — there is a regulatory obligation and an interest in transparency. Nothing
settles against a cleaned historical vol surface, so no such obligation exists. That is
the structural reason the VIX pipeline is vendor-free and an SPX one may not be.

And skew behaviour is central to how vol desks make money, so the precise measurement is a
competitive input. It is sold, not given away.

### But the expensive part may already be in-house

**This desk builds vol surfaces.** That is the costly piece, and it exists here. So the ask
is not "buy a feed" but **"keep the output you are already producing."** One of three is
true:

| situation | what the ask becomes |
|---|---|
| history already stored | a query |
| inputs stored, outputs not | a backfill: re-run the existing fitter over historical chains |
| nothing stored | start saving today; the model improves over time |

Establish which before assuming procurement is involved.

### Free fallback, if it comes to that

**CBOE's SKEW index** — free, daily, back to 1990. A single number summarising SPX tail
skew. Far coarser than a surface, but the response of SKEW to SPX moves could be measured
to give a first-order shift rule. Combined with VVIX, already in this repo, that supports a
defensible first version while surface history is sorted out.

Precedent: the VIX model itself ships with a free VVIX fallback for the vol-of-vol layer
when the Bloomberg historical file is absent, and the cost is about 7%.
