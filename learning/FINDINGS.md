# Findings for the Monday quant review

Status: open. Raised by working through the model 2026-09-17/18.
Meeting with quant: Monday 2026-09-21.

---

## Finding 1 — No skew: far-OTM calls marked at effectively zero

**What.** The pricing layer uses a single at-the-money implied vol per tenor
(`vixshock/shock.py` fills `vol` from the vol-of-vol curve). There is no skew
surface. For strikes near the money this is fine. For far-OTM calls it is not.

**Evidence.** Book holds 90-day calls struck at 100. Market premium 4 cents.

| | |
|---|---|
| market premium | 0.0400 |
| model premium at its ATM vol (72.5%) | 0.000009 |
| ratio | 4,390x |
| vol implied by the 4c market price | 126.9% |
| **skew vs ATM** | **+54.4 vol points** |

**Asymmetry — lead with calls, not puts.** VIX call skew is steep (crash
insurance, persistently bid); put skew is mild and VIX is floored near 9 in
reality, which the model's own `VIX_FLOOR = 9.0` approximates.

| | model px | market-ish px | ratio |
|---|---|---|---|
| call K=30 (1.55x fwd) | 0.471 | 1.120 | 2x |
| call K=50 (2.58x) | 0.015 | 0.514 | 34x |
| call K=100 (5.16x) | 0.000009 | 0.267 | ~29,000x |
| put K=12 (0.62x) | 0.234 | 0.478 | 2x |
| put K=10 (0.52x) | 0.066 | 0.245 | 4x |

**P&L impact, per contract, short the 90d 100-strike call:**

| SPX | model P&L | skew-aware P&L | understated by |
|---|---|---|---|
| -10% | -0.218 | -1.538 | 1.32 |
| -20% | -1.801 | -5.281 | 3.48 |

Model shows roughly one third of the skew-aware loss. We are SHORT, so this
understates the liability — the documented failure mode.

**NOT a code defect.** Verified: pricer returns finite values at strikes 1 to
1000, and put-call parity holds to ~1e-15 at every strike. The code does what it
was told; it was told the wrong vol.

---

## Finding 2 — The shock cannot reach the strikes (structural)

90-day forward across the entire configured shock grid: **12.46 to 42.22**
(base 19.37). Even a -30% SPX shock only reaches 55.28.

**A call struck above ~42 never goes in the money in any modelled scenario.**
Those positions contribute ~nothing to the risk measure regardless of pricing.

They pay off in vol-market dislocations — which is precisely the regime the
model documents itself as underestimating:

- 2018 Feb 5 (XIV day): SPX -7.8%, realised CM-30 +17.5, model +12.7, **ratio 0.72**
- 2024 Aug yen-carry: SPX -8.5%, realised CM-30 +17.1, model +13.8, **ratio 0.80**

Both are short-vol unwinds with a vol-market-structure component SPX does not
explain. README limitation 2 ("single-factor shock") names this. Our book sits
on top of it.

---

## Finding 3 — Stale data passes silently (separate issue)

`python run.py` only downloads when `data/raw/vx/` is **empty**:

```python
elif not data_sources.VX_DIR.exists() or not any(data_sources.VX_DIR.glob("*.csv")):
    data_sources.download_everything()
```

No gate checks data freshness. Run it in two weeks and it prints ALL GATES PASS
using stale curves. The only clue is the `as of` line buried in report section 7.
`--refresh` re-pulls, but requires network.

**Ask:** add a freshness gate — FAIL if the latest curve date is more than N
business days behind today.

---

## The fix available today (no model change)

`vixshock/shock.py` only falls back to the ATM curve when the book's `vol`
column is missing or null. The book format accepts optional `forward` and `vol`
columns per position.

**So:** back out implied vols from market premiums using the model's own
`implied_vol()` (`vixshock/pricing.py`), populate the `vol` column, and base
marks become **exact**. Verified round-trip: 0.0400 -> 126.92% -> 0.040000.

### Two caveats to state up front

1. **The vol-of-vol shock is an additive parallel shift.** It adds the same vol
   points regardless of starting vol:

   | scenario | vol pts added | ATM case | our OTM case |
   |---|---|---|---|
   | SPX -10% | +43.4 | 72.5% -> 115.9% | 126.9% -> 170.3% |
   | SPX -20% | +59.0 | 72.5% -> 131.5% | 126.9% -> 185.9% |

   Real far-OTM call skew **flattens** as the forward rises toward the strike,
   so this likely overstates shocked vol on those strikes. Conservative for a
   short position, but an approximation — say so before someone else does.

2. **It does not change Finding 2.** Correct marks != tail coverage.

### Operational warning

Populate `vol` for **every** position or none. The code fills only the *missing*
entries from the ATM curve, so a partially-populated column silently produces a
book mixing market vols and ATM vols, with nothing in the report flagging it.
Same applies to `forward`.

---

## What would actually close the gap (ascending cost)

1. **Per-position vols in the book file** — fixes base marks today. Does not
   address reachable range.
2. **A skew surface** — vol as a function of moneyness and tenor, so shocked
   strikes get shocked-appropriate vols. Needs a surface data source. Standard
   answer.
3. **A second risk factor** — a vol-specific shock independent of SPX, which is
   what would generate the Feb-2018 state. Model redesign, not a patch. This is
   the honest answer to "why doesn't this cover our tail."

Ask for 1 now. Name 3 so nobody thinks 1 solved everything.

---

## Framing for Monday

Do **not** say "this model is a fail." The curve shock is sound and validates
well (clears 2008 at ratios 1.6-4.6, COVID at 1.2). The gap is pricing coverage
for our strike profile, plus a documented single-factor limitation our book
happens to sit on.

> "Two issues, different severity. First, the pricing layer uses one ATM vol per
> tenor with no skew. Our far-OTM calls are marked at effectively zero against
> real market premiums — orders of magnitude at the base level, before any shock.
> Fixable today with no model change: the book file already supports a
> per-position vol column and the code uses it when present.
>
> Second, and structurally: no scenario moves the 90-day forward above ~42, so
> calls struck above that are never in the money in any modelled state. They pay
> off in vol-market dislocations, which is the regime the model documents itself
> as underestimating — Feb 2018 at 0.72, Aug 2024 at 0.80.
>
> The curve shock itself validates well and I'm not challenging it. Puts are much
> less affected: VIX skew is mild on the downside and the floor at 9 approximates
> the real one."

---

## Caveats on these numbers

- Forward 19.370 and ATM vol 72.5% are the 90-day values from the run dated
  2026-09-16. Substitute current marks.
- "market-ish px" uses a crude skew proxy, not real quotes. The 4c data point
  implied 126.9%, close to what the proxy gives, but use real market vols in
  anything shown to others. Direction and order of magnitude are solid; the
  specific figures are illustrative.
- Skew held constant through the shock (see caveat 1 above).

---

## Finding 4 — No starting-level dependence (added 2026-09-18)

**What.** `ResponseParams.dvix(r, T)` takes exactly two inputs: the SPX shock and
the tenor. Nothing else. The starting VIX level (`vix0_30` etc.) IS computed in
`join.py` but the comment says it outright: *"starting level, kept for
diagnostics."* It is never used in the fit. `shock.py` reads no SPX data at all.

This is README limitation 5, but the consequence is not spelled out there.

**Why it matters — how VIX actually reaches extremes.**

| date | spot VIX | CM-30 | SPX 20-day | SPX drawdown from high |
|---|---|---|---|---|
| 2020-03-18 | 76.45 | 69.87 | −29.2% | −29.2% |
| 2008-11-20 | 80.86 | 65.68 | **−17.1%** | **−50.4%** |
| 2008-11-19 | 74.26 | 62.56 | **−10.1%** | **−46.8%** |
| 2008-11-21 | 72.67 | 62.11 | **−8.8%** | **−47.2%** |

In 2008 VIX hit its highs on 20-day drops of only 9–17%, because the market was
already ~47% off its highs and VIX was already near 60. **VIX reaches extremes
through base-level escalation, not through a single large move.**

**The response IS level-dependent — measured.** Windows with SPX drops of 8–15%,
split by starting VIX:

| starting VIX | n | median dVIX | median ENDING level |
|---|---|---|---|
| 0–15 | 54 | 10.0 | 23.9 |
| 15–20 | 362 | 8.8 | 25.3 |
| 20–25 | 418 | 7.5 | 30.0 |
| 25–35 | 299 | 5.7 | 32.3 |
| 35+ | 359 | 5.8 | **48.5** |

Points added SHRINK with a higher base; ending level CLIMBS. The model uses one
number regardless.

**The consequence nobody wrote down.** Because the model adds a fixed number of
points to today's level (~18), it can never produce an ending level near the
historical maximum. −20% lands at 53; the all-time CM-30 high is 69.87. There is
no path in this model to the levels where far-OTM calls pay off.

**Historical bounds, for context:**
- Spot VIX all-time high **82.69** (2020-03-16). Only 3 days above 80 ever.
- CM-30 all-time high **69.87** (2020-03-18). Only 7 days above 60 ever.
- So a 100-strike call has never been in the money at expiry.

**DO NOT lead with "VIX never hit 100 so we're safe."** A quant will answer that
22 years is not a bound, VIX is unbounded above, and 2020 was novel. Lead with
the MECHANISM: VIX reaches extremes via base escalation, and the model has no
base-level dynamics. Structural claim, not empirical.

**The fix needs no new data.** `vix0` is already computed on every row of the
fitted dataset. It needs a refit, not a new feed. (Which is harder than it should
be — see ARCHITECTURE_GAPS.md GAP 1.)

---

## THE PATTERN THAT TIES IT TOGETHER

Say this Monday. It is stronger than any single finding:

> **This model has excellent gates for the failures its author anticipated, and
> no gates at all for the ones he didn't. It fails quietly.**

Evidence:
- Stale data → prints ALL GATES PASS anyway
- Far-OTM calls → marked at effectively zero, no warning
- Bloomberg-free path → crashes with an obscure pandas error
- Partially-filled `vol` column → silently mixes market and ATM vols, nothing flags it

**A risk model that silently produces wrong numbers in the environment where it
is actually used is worse than one that refuses to run.**

---

## THE FOUR FINDINGS, SEPARATE

Keep these apart. Presented separately they are four credible observations from
someone who read the code carefully. Blurred together they sound like distrust.

| # | finding | type | fix |
|---|---|---|---|
| 1 | No skew — far-OTM calls marked at ~zero | pricing input | add `vol` column to the book — no code change |
| 2 | No starting-level dependence | model specification | refit using `vix0`, already computed |
| 3 | Refits everything to price one book; no freshness gate | architecture | split calibrate from price |
| 4 | Bloomberg dependency + `DATE`/`date` bug | deployment | one line + config change |

**What NOT to say:** "this model is a fail." The curve shock validates well —
median ratio 1.67 across 14 crises, clears every 2008 leg at 1.8–2.8x, COVID at
1.2, 94.6% coverage of all historical selloffs beyond 2%. The maths is sound.
The packaging, the pricing inputs, and one specification choice are what need work.
