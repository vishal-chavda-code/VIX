# Questions for the quant review — 2026-09-21, refreshed 2026-09-26

Everything below is a decision the model owner cannot make alone. Each item
gives the number that raised it, where it comes from, and the choices. The
maths behind the numbers is in `output/report_<latest>.txt`; every figure here
is printed there.

The model itself validates well and is not the question: median model/realised
ratio 1.67 across 14 stress episodes, every 2008 leg covered at 1.6–4.6×, COVID
at 1.2×, 94.6% of all historical sell-offs beyond 2% at or below the model.

**What changed on 2026-09-26.** The book now holds **SPX options as well as VIX options**,
and it is **net long SPX puts plus long and short VIX calls — so its loss scenario is an
SPX rally, not a selloff.** Most of the model's conservatism was aimed at the down branch.
The SPX leg is now built (vol shock included), P&L is in currency, and the up branch can be
fitted to its conservative envelope. That moved the centre of gravity of this review:

- **Q1** (starting-level dependence) is still the main modelling question — and it now bites
  on the *rally* side too, through **Q5**.
- **Q5** is the most urgent: in the book's loss scenario a hardcoded floor sets a large
  share of the answer.
- **Q13–Q16** are the assumptions the SPX leg rests on.

Engineering state, for context: 209 tests (including a regression test that the rebuilt
pricing layer reproduces the old one to machine precision on a VIX-only book), strict
book-input validation, a position key, ISO-only dates, pricing as of the book's own date
with a hard gate on the market data date, calibration separated from pricing, every run
stamped with the parameters and market-data dates that produced it.

---

## Q1. Starting-level dependence (model specification) — the main question

The model adds a fixed number of VIX points for a given SPX drop, regardless of
where VIX starts. `dVIX(r, T) = beta_T · h(r)`; VIX level is not an input.

| base VIX curve | −20% SPX → CM-30 | comment |
|---|---|---|
| 2026-09-18 (CM-30 = 17.75) | **52.4** | plausible; history max for the 30-day future is 69.9 |
| 2020-03-16 (CM-30 = 59.15) | **93.8** | above any VIX future ever traded |

So from a calm base the model is accurate-to-conservative; from an already-
stressed base it overshoots. The book will be run daily, including in ugly
markets, so the second row is a real operating state, not a corner case.

**The same flaw now shows on the rally side** (Q5): from a calm base the up branch
subtracts more points than VIX has room to fall, and a floor catches it.

The data to fix this is already computed: every fitted window carries the
starting level (`vix0_<T>` in `data/processed/dataset_pooled.csv`). Options:

- **(a)** make `beta` a function of starting VIX (e.g. `beta_T · (V_ref / V_0)^p`),
  fitted the same envelope way;
- **(b)** apply a ceiling to the shocked future (history: 30-day future max 69.9,
  120-day max 50.3) — crude, but explainable in one sentence;
- **(c)** leave it: overshooting from a high base is conservative.

Owner's preference: accuracy over conservatism. **Which of (a)/(b)/(c), and if
(a), what form would you accept?** Note (a) would address Q5 at the same time.

## Q2. Envelope quantile — 0.95 or strict max?

The down branch is fitted to the 95th percentile of the VIX response in each
1% SPX-return bucket. Two episodes sit above it at the 30/60-day points:

| episode | SPX | realised CM-30 | model | ratio |
|---|---|---|---|---|
| 2018-02-05 (Volmageddon), 6-day window | −7.8% | +17.5 | +12.7 | 0.72 |
| 2018-02-05 alone | −4.1% | +14.8 | +6.5 | 0.44 |
| 2024-08-05 (yen carry), 14-day window | −8.5% | +17.1 | +13.8 | 0.81 |
| 2024-08-05 alone | −3.0% | +9.3 | +4.8 | 0.51 |

Both are short-vol unwinds from a very low VIX base — modest SPX move, huge VIX
move. Strict max (`ENVELOPE_QUANTILE = 1.0`) raises −20% at CM-30 from 34.6 → 41.7
VIX points and covers both episodes (Feb 2018 ratio 1.22, Aug 2024 1.34 — measured
2026-09-26; an earlier note that it misses Feb 2018 did not reproduce). **Is 0.95 with
two documented exceptions acceptable, or do you want strict max, or an explicit
vol-shock add-on for the low-VIX regime?** (This interacts with Q1: both misses are from
a low base.)

## Q3. Calibration window — full sample or post-2012?

| window | beta_0 | −20% → CM-30 |
|---|---|---|
| 2004–now (default) | 198 | +34.6 |
| 2013–now | 233, k ≈ 0 (linear) | +37.6 |
| 2004–2012 | 249, k < 0 (saturating) | +19.4 |

The futures response roughly doubled after VIX ETPs appeared. The spec said
full sample with a stability check; the check shows the shift. Post-2012 is
more conservative on the down side and arguably the only regime that still exists.
**Which window?** One line in `config.py` (`FIT_START`).

## Q4. ~~Scenarios beyond −30% are extrapolation~~ — moot

The scenario grid was trimmed to ±5/10/15/20% on 2026-09-26. The book's loss side is a
rally, and beyond +20% every front-tenor answer was set by the floor rather than the model.
The 14 stress episodes still run out to −40%.

## Q5. The rally side: which envelope, and what to do about the floor — MOST URGENT

**The estimator (decided, LIVE since the 2026-09-26 calibration).** The up branch was a least-squares (average) fit.
For this book a rally is the loss scenario, so it is now fitted — in code — to the **lower
envelope** (the 5th percentile of the VIX response per 1% rally bucket, `UP_BRANCH_FIT =
"envelope"`), mirroring the down branch. The form stays linear. Measured on the same data:

| up branch | `beta_up_0` | `lam_up` | +10% → CM-30 | +20% → CM-30 | CM-30 coverage of >2% rallies |
|---|---|---|---|---|---|
| least squares (frozen, in force) | 68.3 | 0.218 | −5.5 | −11.0 | 54.2% |
| lower envelope q = 0.05 (**live**) | 169.8 | 0.277 | −12.9 | −25.8 | 94.2% |

Every calibration gate passed with it and the other eight numbers are bit-identical. Two
things the review should know about it:

- **The up envelope fits less well than the down one.** R² on the envelope points is ~0.71
  at CM-30 against 0.95 for the down branch, and it collapses at CM-150 (R² −0.23, coverage
  75%). The large-rally buckets (+14% and up) are post-crisis rebounds from VIX 60–80,
  with 5th percentiles of −25 to −33 points.
- **Applied from a calm base it goes through zero.** From CM-30 = 17.75, +20% is −25.8
  points: a VIX of −8. Least squares already reaches 6.8.

**The floor.** `VIX_FLOOR` (9.0) catches it. With the envelope live, on the template book:

| scenario | positions floored | largest lift | P&L the floor sets |
|---|---|---|---|
| +10% | 2 | 3.5 pts | +0.7k |
| +15% | 4 | 9.8 pts | **+47.7k** |
| +20% | 4 | 16.0 pts | **+89.3k** |

A hardcoded constant is setting a large share of the loss-scenario number. Every run now
reports this per scenario. The two options:

- **(a) keep 9.0, justified and reported.** 9.0 is just under the lowest spot VIX close ever
  (9.14, 2017-11-03). 8.75 was considered and rejected: it was a contract's final settlement on
  its expiry morning — spot's opening print, not a traded futures level. With a day or more
  left no VIX future has settled below 9.88; with 30+ days, never below 11.32. Transparent, one sentence to explain; but it is a clip, and a clip is where the
  answer is being set.
- **(b) an asymptote: the response decays toward the floor instead of being clipped**, e.g.
  `dVIX_up = −(F − L) · (1 − exp(−beta_T · r / (F − L)))`. Same slope for small moves, never
  crosses L. But it is new curvature (P0.1 said keep the up branch linear), it makes the
  response depend on the starting level — which is Q1 — and it should be fitted and
  validated, not bolted on at pricing time.

**Decided 2026-09-26: (a), at 9.0, with the envelope up branch live.** (b) is folded into
Q1(a) — a level-dependent beta fixes both sides at once. **Do you agree with the order: floor
now, level dependence as the proper fix?**

## Q6. Book inputs — LARGELY RESOLVED, one part remains

*Resolved.* The book takes a **premium** per position and the pricer backs the implied vol
out of it with its own forward, day-count, rate and Black-76, so base marks are exact by
construction. The **`book_vols` gate FAILS the run** if any VIX position falls back to the
ATM curve; an SPX position without a premium is rejected outright. Each VIX option's
**forward** is now the settle of the VIX future expiring with it, downloaded that day, with a
`book_forwards` gate against falling back to the interpolated curve: an interpolated forward
was invisible in the base price and wrong in the shock (Oct 2026 future settled 18.04; the
curve said 17.84). Every mark records its `premium_source`, and
zero-bid marks are flagged.

**What remains a question:** the vol-of-vol shock is an **additive parallel shift**, so a
far-OTM call keeps its full skew premium through the scenario. Real skew flattens as the
forward rises toward the strike (today the 100 strike is 5.2× the forward; after −20% it is
2.4×), so shocked vols on those strikes are likely **overstated** — conservative for a short
position, but an approximation. **Is a parallel shift acceptable, or should the shift decay
with moneyness?**

## Q7. Vol-of-vol data provenance

The vol-of-vol layer was calibrated once on a frozen file of Bloomberg tenor
implied vols (`data/bloomberg_historical/`, documented there; nothing calls
Bloomberg). The alternative is free VVIX only, ~7% lower shocks, tenor fade
assumed. **Is a frozen one-time Bloomberg load acceptable for provenance, or
should it be VVIX-only?**

## Q8. Positions past the last tenor — PARTLY RESOLVED

CM tenors run to **150** days (180 was tried and rejected by the model's own stress gate:
3 understated episodes at T180 against a threshold of 2, plus 71 gap days).

*Resolved:* a VIX position's **forward** is now its own listed future's settle, so a 400-day option is no
longer priced off the flat-extrapolated 150-day curve point.

What remains: the **shock** for such a position is still read at its own tenor, where
`beta_T` has decayed well past the fitted range. Such positions are flagged
(`beyond_calibrated_tenor`) and counted in every run. Long-dated SPX puts hit the same
extrapolation through the SPX vol shock. **Warn (as now), or fail a gate?**

## Q17. The vol-of-vol up branch

`UP_BRANCH_FIT` governs the VIX-level response only; the vol-of-vol fit is pinned to least
squares. Which tail is conservative for VIX-option vol in a rally depends on the long/short
VIX call mix, which varies. **Fit it to an envelope too — and which one — or keep it at the
average?**

---

# SPX OPTIONS — built 2026-09-26

SPX needs **no response function**: the scenario *is* the underlying move. The leg adds no
fitted numbers. What it rests on is below.

## Q9. The SPX forward — RESOLVED as a flat carry, pending your view

No matched call/put pairs exist in the book (it is all SPX puts), so put-call parity is
unavailable; Bloomberg is out; CBOE does not publish SPX forwards. Before building a futures
pipeline we measured whether it matters: deep-OTM SPX puts (28–365 days, 70–95% moneyness)
priced with F = spot and again with F = spot × 1.005, the vol inverted from the same premium.

| scenario | worst P&L difference, as % of that put's P&L |
|---|---|
| rallies +5 / +10 / +20% (the loss side) | 2.3% / 1.5% / 0.6% |
| selloffs −5 / −10 / −20% | 7.8% / 7.3% / 6.3% |

The error largely cancels because the vol is inverted from the same forward. So the forward
is **SPX close × exp((rate − dividend yield) × T)** with a documented 1.3% yield; a book-
supplied forward overrides it. **Acceptable, or do you want ES-futures-implied carry?**

## Q10. How should the SPX vol surface move under a shock? — ANSWERED PROVISIONALLY

Built as: each position's own implied vol (from its premium) shifted in parallel by
`SPX_VOL_SCALE × dVIX_T(r) / 100` — the existing VIX response reused, since VIX is 30-day
SPX implied vol. No second calibration. Q13–Q16 are the assumptions inside that.

## Q11. Two years of surface history — enough? (for a proper SPX vol model later)

If the provisional vol shock is to be replaced by a measured one, we may be able to get
~2 years of surfaces. Measured against SPX over that window (502 trading days):

| pooled 1–20d windows | count |
|---|---|
| below −5% | 286 |
| below −10% | 41 |
| **below −15%** | **0** |
| **below −20%** | **0** |

Worst 20-day drawdown: **−12.1%** (April 2025). That supports a shift rule for −5% to −12%
and establishes the machinery, but not the −20% end of the grid.

**Proposal: take the two years, and separately backfill ~60 days either side of Feb 2018,
Q4 2018, COVID, 2022, Aug 2024 and Apr 2025** — ~600 extra days rather than a decade, and
where the calibration information lives. **Is that backfill feasible?**

## Q12. Joint P&L is scenario-consistent, not a correlation model

Combining both books asserts that at SPX −20% the index falls 20% **and** the VIX front
rises ~35 points, together, deterministically, with no dispersion. That becomes
**load-bearing** now that the two legs sit in one book: the model will show any hedge
between them working perfectly in every scenario, by construction. Feb 2018 is the
counter-example — SPX −8% with VIX +17.5, which the model underestimates at a ratio of 0.72.

**Is scenario-consistent joint P&L acceptable for the intended use, or does the portfolio
measure need a genuine joint distribution?** (The contract multiplier this needed is done:
P&L is in currency, per product and total.)

## Q13. `SPX_VOL_SCALE` = 1.0

VIX is a variance-swap level and runs above ATM vol, and VIX futures carry a convexity
wedge. Both bite the *level*, and only the *change* is used — but the change in ATM vol for
a given change in VIX may still be below 1 (and differs for OTM puts). **What scale would you
accept, and on what evidence?** It is one number in `config.py`.

## Q14. Tenor mapping of the SPX vol shock — SETTLED 2026-09-26: average over the option's life

As first built, a T-day SPX option's vol moved by `dVIX_T`: the move in the VIX future *expiring*
at T. That future settles to 30-day vol from T to T+30 — but a T-day option's vol covers days
0 to T, i.e. the futures from tenor 0 to T−30. Because the response fades with tenor, the
endpoint understates the option's vol move:

| option tenor | vol move understated by (lsq / envelope up branch) |
|---|---|
| 1 month | 1.2× / 1.3× |
| 3 months | 1.6× / 1.8× |
| 6 months | 2.3× / 2.9× |
| 1 year | 5.3× / 9.0× |

On the template's SPX puts that is a 10% smaller loss at +5% (−162k vs −179k with the
envelope up branch), shrinking to nothing by +20% where the puts are worthless either way;
on the selloff side the window version is ~37% larger.

**Switched to the window average** — the fitted response averaged over tenors 0..T−30, exact
for the exponential fade, same fitted numbers (`portfolio.spx_vol_shock`, tested against a
brute-force average). What remains for you: tenor 0 is the futures fit extrapolated to spot,
and spot VIX moves more than that in stress, so the front of the SPX vol shock is if anything
still understated. **Is that acceptable, or should the front be anchored to spot VIX's own
response?** (That would need a fit of spot VIX against SPX — a new calibration.)

## Q15. Sticky-strike, not sticky-moneyness

The book carries one vol per position, not a surface, so the skew cannot move with
moneyness: the implementation is sticky-strike plus a parallel ATM shift. For long puts in a
rally that is the conservative side (under sticky-moneyness the put drifts further out, where
the skew gives it more vol); in a selloff it is the generous side, which is this book's gain.
**Acceptable given the book's direction?**

## Q16. `VOL_FLOOR_SPX` = 5%

The old single floor was 20%, chosen for VIX options (vols 70–130%, never binds). SPX ATM vol
runs 10–13% in a calm tape, so 20% would have clamped the rally vol collapse that is this
book's main loss. 5% is below any SPX implied vol on record (VIX low 9.14). It binds on the
template only slightly (≤ 0.15 vol points at +20%), and every binding is reported. **Is 5% the
right floor, or should it be tied to a historical minimum of ATM vol by tenor?**

---

## Things that are settled and not questions

- Down branch on the upper envelope, not least squares (why: understating is the failure mode).
- Each branch's envelope is chosen for the book's exposure, and `config.py` names the live tail.
- Built on futures, not spot (why: options settle on futures).
- Windows of 1–20 days pooled (why: −20% never happens in a day; fixed horizons dilute the tail).
- Vol-of-vol moved with the shock (why: holding it fixed understates).
- Pricing as of the book's own date, with the market data required to be from that exact date.
- Every floor binding is reported with the P&L it sets; no constant acts silently.
- No vendor dependency: history frozen on disk; each day's settles and SPX close downloaded from public CBOE files (no key), with local CSVs as the fallback.
- Every run prints its own parameters, fit quality per branch, stress table, flags and gates.
