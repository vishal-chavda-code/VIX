# Adding SPX options — design note

Planning only. No code written. Written 2026-09-19.

Goal: price a book containing **both** VIX options and SPX options under the same SPX
scenario grid, and report one joint P&L.

---

## Why the pricer itself is easy

Roughly **15% of the work the VIX model took**, because three of its hardest parts are
simply not needed:

| VIX needed | SPX needs |
|---|---|
| a **response function** — 22 years of history, envelope fit, pooling, stress validation, to answer "where does VIX go when SPX moves?" | **nothing.** The scenario *is* the underlying move. SPX −20% means the SPX forward is 20% lower. No calibration. |
| **constant-maturity construction**, because futures roll | nothing — options reference the index directly |
| a **vol-of-vol layer**, because VIX-option vol is its own animal | a skew surface, which is standard and well understood |

`pricing.py` already works: Black-76 takes a forward, and SPX options price off a forward
exactly as VIX options do.

**The hard parts are not the pricer.** They are data, skew, and the multiplier.

---

## BLOCKER 1 — the data does not exist here today

The repo has `data/raw/spx.csv`: one column, the index close. That is enough to
*calibrate* the VIX response. It is nowhere near enough to *price* SPX options.

Per option, per day, a pricer needs:

| input | have it? | where from |
|---|---|---|
| strike, expiry, type | yes | the book |
| **premium (mid)** | **NO** | needs an option feed |
| **forward per expiry** | **NO** | implied from option prices, or built from rates + dividends |
| rate | roughly | `config.RISK_FREE_RATE` |
| index level | yes | `spx.csv` |

### This is a different problem from VIX

The VIX pipeline is vendor-free because **CBOE publishes VIX futures settlements for
free** — that is where the forward comes from. **SPX options have no listed forward and
no free, reliable option-quote source.** Adding SPX almost certainly means a data
subscription. That is a procurement conversation, not an engineering one, and it should
be raised as such.

### Sources, in order of preference

1. **The existing desk feed — check this first.** SPX option marks are far more commonly
   available than VIX ones. If the desk marks an SPX book daily, that source already has
   premiums and probably forwards. Requires no new procurement.
2. **CBOE DataShop** — authoritative (CBOE lists SPX options). End-of-day quotes with
   bid/ask/settlement per strike and expiry, downloadable daily. Paid. Reputable enough
   for model validation, which matters for the governance story.
3. **OPRA-derived vendors** (ORATS, iVolatility, Polygon, etc.) — consolidated option
   data including implied vols and forwards. Cheaper, API-based, quality varies.
4. **Free sources (Yahoo and similar) — do NOT use.** Delayed, gappy chains with
   unreliable bid/ask. Fine for the SPX index level, which is calibration-only. Not fine
   for marking a book.

### Getting the forward without a dividend forecast

If premiums are available but forwards are not, **imply the forward from put-call
parity** rather than constructing it:

```
forward = strike + (call_price − put_price) × e^(rT)
```

using a near-the-money call and put at the **same strike and expiry**.

This is the same principle as preferring premium over vol: **take the observable.** The
market's forward already embeds dividends, borrow cost and financing, so no dividend
forecast is needed and no assumption can be wrong.

The alternative — spot × (1 + rate − dividend yield) — requires a rate curve *and* a
dividend forecast, and the dividend forecast is a real modelling choice (discrete
dividends, borrow, index effects). Avoid it if the data allows.

**Practical requirement:** the feed must supply a call *and* a put at one common strike
per expiry. A modest ask if premiums are being pulled anyway.

### On dividends

Dividends live **in the forward**, not in Black-76. Once the forward is right, the pricer
needs no dividend input at all. So "are dividends handled?" reduces entirely to "is the
forward right?" — which is why implying it is the safer route.

---

## BLOCKER 2 — no contract multiplier exists anywhere

Verified: there is no multiplier in the codebase. P&L today is
`(shocked_price − base_price) × quantity`, i.e. **index points × contracts**, not currency.

Fine for one product. **Impossible for two** — VIX points and SPX points cannot be summed.

**This must be fixed before a joint report means anything**, and it improves the VIX model
on its own. Add a `multiplier` book column with per-asset defaults (VIX options $100, SPX
options $100, but SPX futures $50 and mini contracts differ), apply it in `reprice_book`,
and report in currency.

Small change, and it is a prerequisite for everything else here.

---

## BLOCKER 3 — SPX skew is not optional

**Skew:** out-of-the-money puts trade at higher implied vols than out-of-the-money calls.
Plot vol against strike and the line slopes down. Everyone owns equities and buys puts for
protection, and crash risk is real, so the downside is bid.

**The two products skew in opposite directions:**

| | which side is bid | why |
|---|---|---|
| **SPX** | **put** skew — low strikes | people fear the index falling |
| **VIX** | **call** skew — high strikes | people fear vol exploding |

Same underlying behaviour — everyone hedges the same direction — but VIX moves opposite to
SPX, so the bid lands on the other side. The far-OTM VIX calls in the current book at 127%
vol against a 72.5% ATM *are* VIX call skew.

For VIX, ATM-only pricing was survivable for most positions and the far-OTM failure was a
*finding*. **For SPX index options, skew is the dominant feature** and put skew is steep —
it is the whole reason those puts are held. ATM-only would be wrong across a much larger
share of a typical book.

**And the surface must move with the scenario.** SPX −20% does not merely lower the index;
it lifts the whole surface and steepens the skew. Holding the surface fixed would badly
understate long-put P&L. This is the analogue of the VIX model's vol-of-vol layer — and
that is the weakest part of that model, which is a fair warning about the effort involved.

**Mitigation, same as VIX:** if the feed supplies a premium per position, the vol is backed
out per position and the *base* marks are exact with no surface at all. A surface is then
only needed for the *shocked* vol. That reduces the problem considerably and should be the
first version.

---

## Premium vs vol — identical logic to VIX, and more important

Premium and vol are two encodings of the same information: Black-76 is invertible, so
given forward, strike, time and rate, price ↔ vol is one-to-one.

Supplying a premium is not giving the model less. It is giving the same information in the
form the market quotes, and letting the model convert with **its own** forward, day-count
and rate — so the mark reproduces the premium exactly.

An imported vol carries whoever produced it's conventions. Measured on VIX: a vol struck
against spot rather than the future implied 133.80% versus the correct 126.92%, and a **59%
price error** when consumed.

**For SPX this matters more, not less**, because the forward contains dividends. Two
systems disagreeing on the dividend assumption produce different vols from the same price.
Take the premium.

---

## Proposed shape

The calibrate/price split already built is the right seam.

```
price.py --book <file>
    │
    ├── read book, split on an `asset` column: VIX | SPX
    │
    ├── VIX positions ──► existing path, unchanged
    │                     forward = CM curve, shocked by the response function
    │                     vol = premium-implied, shocked by vol-of-vol
    │
    ├── SPX positions ──► new path
    │                     forward = implied from put-call parity, shocked directly
    │                               by the scenario (no response function)
    │                     vol = premium-implied, shocked by a surface rule
    │
    └── join on `scenario`, apply multipliers, aggregate to one currency P&L
```

**Reusable unchanged:** `pricing.py` (Black-76 and `implied_vol` are asset-agnostic), the
whole book-input contract and `validate.py`, run folders, manifests, gates, provenance,
and `config.SHOCKS` — the same grid drives both.

**New:** an SPX forward function, a skew surface plus a shift rule, currency conversion.

---

## The modelling caveat that must be stated in the output

Combining the two books gives **scenario-consistent P&L, not a correlation model.**

At SPX −20% the model asserts that the index falls 20% *and* the VIX front rises 34 points,
together, deterministically, with no dispersion. That is the same single-factor assumption
already in the VIX model — but it becomes **load-bearing** once the two books are meant to
hedge each other, because the model will show the hedge working perfectly in every
scenario by construction.

Feb 2018 is the counter-example: SPX −8% with VIX +17.5, a relationship the model
underestimates at ratio 0.72.

This is a natural extension of Q1 in `QUESTIONS_FOR_QUANT.md` and should be raised in the
same conversation.

---

## Sequencing

| # | step | effort | note |
|---|---|---|---|
| 1 | **Contract multiplier, currency P&L** | small | prerequisite; improves the VIX model alone |
| 2 | **Secure an SPX option data source** | procurement | the real blocker — start here in parallel |
| 3 | `asset` column and routing | small | plumbing |
| 4 | SPX forward via put-call parity | small | needs a call+put at a common strike per expiry |
| 5 | SPX Black-76 pricing, premium-implied vols | small | genuinely an afternoon |
| 6 | Skew surface + scenario shift rule | **large** | where the effort concentrates |
| 7 | Joint report with the correlation caveat | medium | cross-asset aggregation |

Steps 1–5 give a working SPX pricer with exact base marks (from premiums) and an
approximate shocked vol. That is a usable first version. Step 6 is the real project.

---

## What to say when proposing it

> *"The pricer is easy — SPX needs no response function, because the scenario is the
> underlying move. Three things are not easy.*
>
> *First, we do not have the data. We hold the SPX index level only. Pricing options needs
> per-strike premiums and a forward per expiry, and unlike VIX futures there is no free
> reliable source. Either the desk feed already has it, or this needs a subscription.*
>
> *Second, there is no contract multiplier in the code — P&L is in index points today.
> That is fine for one product and impossible for two.*
>
> *Third, SPX skew is steep and matters far more for index options than it did for VIX,
> and it has to move with the scenario.*
>
> *And one modelling point to be explicit about: combining the books gives
> scenario-consistent P&L, not a correlation model. We would be asserting SPX −20% and VIX
> +34 happen together with no dispersion — the same single-factor assumption already in
> the VIX model, but load-bearing once the two books are meant to hedge each other."*

---

# REVISION 2026-09-19 — after checking the desk's data situation

Three things changed from the assessment above.

## The data blocker is probably not a blocker

The desk has a vendor options feed with strike, time to expiry, a computed rate, vol and
premium, and the team **build vol surfaces as their job**. That is exactly what this plan
calls a "skew surface", so BLOCKER 3 largely dissolves: consume their surface rather than
build one.

What remains unknown, and is worth asking rather than assuming:

| question | why it matters |
|---|---|
| Is the feed **full chains** or **only held positions**? | positions-only is enough to price the book; full chains also give a surface |
| Is vol **per-strike** or **ATM-per-tenor**? | per-strike means the surface already exists |
| Does it publish a **forward** per expiry? | believed not — if not, imply it from put-call parity |

## The multiplier point, stated precisely

The book does carry number of contracts and the model **does** use it — P&L is
`(shocked − base) × quantity`. What is absent is the **dollars-per-index-point**
conversion:

```
today:        2.00 points × 100 contracts            = 200      (index points)
actual money: 2.00 points × 100 contracts × $100     = $20,000
```

Harmless for one product — a constant factor applied downstream. **Not harmless for two:**
SPX futures are $50, minis differ, and any mixed book cannot be summed without it.

## The better question for the quant

Rather than presenting a design and asking for data to fit it:

> *"Can you provide the data needed for an SPX option pricer, and given what is available,
> which model should we use?"*

Let the available data drive the model choice. The follow-up that matters most:
**how should the vol surface move under an SPX shock?** That is the SPX analogue of the
vol-of-vol layer, and it is a modelling decision the surface builders are best placed to
make.

---

# SEPARATE FINDING — the curve goes flat past 120 days, silently

Not an SPX issue. Found while answering "is the CM curve continuous?"

## What happens

`Curve` interpolates linearly between five knots — spot at day 0, then CM-30/60/90/120 —
and `np.interp` **holds the last value flat** beyond the final knot.

| option tenor | forward used | flagged? |
|---|---|---|
| 5d | 15.30 | fine — interpolated between spot 14.81 and CM-30 |
| 100d | 19.37 | fine |
| 130d | 19.80 | **no** |
| 400d | **19.80** | **no** — identical to the 120-day point |

A 400-day option prices off the 120-day forward and the run exits 0 with no warning. The
short end is fine: the spot anchor at day 0 means sub-30-day options interpolate sensibly.

Two things are wrong past 120 days:

1. **The VIX curve is normally upward-sloping in contango**, so flat extrapolation
   understates the forward and underprices long-dated calls.
2. **The shock is read at that tenor too**, and `beta_T` keeps decaying exponentially well
   beyond where the response function was fitted.

## The data supports going further, and it is free

Measured from the contract files already on disk:

| target | buildable on | days short |
|---|---|---|
| CM-120 (current) | 100.0% of days | 0 |
| CM-150 | 99.9% | 3 |
| CM-180 | 98.7% | 71 |
| CM-210 | 93.7% | 359 |

Minimum reach ever is 147 days; the median is 253. **The 120-day cap is a choice from the
original spec, not a data limit.** Extending costs one line in `config.TENORS` plus a
recalibration.

Caveats: longer tenors are thinner (the README already notes 71% of 2008's 120-day
observations came from contracts trading under 100 lots a day, and it worsens further
out), and CM-180 loses ~1.3% of days to gaps.

## Recommendation

1. **Extend `config.TENORS` to include 150 and 180.** Free, and the data supports it.
2. **Make beyond-the-last-knot explicit** — warn, or fail the `book_vols`-style gate,
   rather than silently returning the last knot.

No positions beyond 120 days exist today. That is precisely when to fix it: "it will come
one day" is exactly the scenario where a silent wrong answer does damage.

---

# REVISION 2 — tenors extended, and the empirical limit found

## CM tenors now run to 150, not 180

Tried 180 first. **The stress gate FAILED**: T180 understated 3 episodes against a
threshold of 2, and 71 days had interpolation gaps. Backed off to 150, which passes every
gate with only 3 gap days.

So the extension was free up to 150 and genuinely not free beyond it. That is an
**empirical boundary discovered by the model's own validation**, not a guess — a good
illustration that the gates do real work.

| tenor | buildable | stress gate |
|---|---|---|
| 120 (was the cap) | 100.0% of days | pass |
| **150 (now the cap)** | **99.9%** | **pass** |
| 180 | 98.7% | **FAIL — 3 understated episodes** |

Flat extrapolation now begins at 150 days instead of 120. The recalibrated front response
moved slightly (beta_0 192 → 201) because the fit now includes the extra tenor.

74 tests still pass.

**Still outstanding:** beyond the last knot the curve is silently flat. A 400-day option
prices off the 150-day forward and the run exits 0. Should warn or gate.

## Asking for historical vol surfaces

The desk builds vol surfaces, so the shift rule can be **measured rather than assumed**.
The initial idea was to request a few dates around known selloffs; counting the data shows
that is too thin.

| history | windows below −5% | below −10% | below −20% |
|---|---|---|---|
| 3 years | 348 | — | — |
| 5 years | 1,288 | — | — |
| **10 years** | **2,265** | **408** | **76** |

Fitting a level shift, a slope change and their tenor dependence from a dozen event dates
would repeat exactly the failure the VIX model documents at single fixed horizons: too few
tail observations, so the fit explodes or collapses.

**Ask for a continuous daily history, 10 years if possible, 5 workable, 3 as a floor.**
A continuous history also lets the existing pooled 1–20 day envelope machinery be reused
unchanged — extending a validated method rather than inventing one.

The full request, written to hand over directly, is in `VOL_SURFACE_DATA_REQUEST.md`.
