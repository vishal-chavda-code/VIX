# Questions for the quant review — 2026-09-21

Everything below is a decision the model owner cannot make alone. Each item
gives the number that raised it, where it comes from, and the choices. The
maths behind the numbers is in `output/report_<latest>.txt`; every figure here
is printed there.

The model itself validates well and is not the question: median model/realised
ratio 1.67 across 14 stress episodes, every 2008 leg covered at 1.6–4.6×, COVID
at 1.2×, 94.6% of all historical sell-offs beyond 2% at or below the model.

**Q1–Q8 concern the VIX model as it stands. Q9–Q12 concern adding SPX options**, which is
scoped but not built — see `learning/SPX_EXTENSION_PLAN.md`. If time is short, Q1 is the
one that matters most, and Q9 is the one that unblocks everything else.

Engineering state, for context: 74 tests, strict book-input validation, ISO-only dates,
calibration separated from pricing, every run stamped with the parameters and market-data
date that produced it. Assessed in `learning/PRODUCTION_READINESS.md`.

---

## Q1. Starting-level dependence (model specification) — the main question

The model adds a fixed number of VIX points for a given SPX drop, regardless of
where VIX starts. `dVIX(r, T) = beta_T · h(r)`; VIX level is not an input.

| base VIX curve | −20% SPX → CM-30 | comment |
|---|---|---|
| today (CM-30 = 17.7) | **52.0** | plausible; history max for the 30-day future is 69.9 |
| 2020-03-16 (CM-30 = 59.1) | **93.5** | above any VIX future ever traded |

So from a calm base the model is accurate-to-conservative; from an already-
stressed base it overshoots. The book will be run daily, including in ugly
markets, so the second row is a real operating state, not a corner case.

The data to fix this is already computed: every fitted window carries the
starting level (`vix0_<T>` in `data/processed/dataset_pooled.csv`). Options:

- **(a)** make `beta` a function of starting VIX (e.g. `beta_T · (V_ref / V_0)^p`),
  fitted the same envelope way;
- **(b)** apply a ceiling to the shocked future (history: 30-day future max 69.9,
  120-day max 50.3) — crude, but explainable in one sentence;
- **(c)** leave it: overshooting from a high base is conservative.

Owner's preference: accuracy over conservatism. **Which of (a)/(b)/(c), and if
(a), what form would you accept?**

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
move. Strict max (`ENVELOPE_QUANTILE = 1.0`) raises −20% at CM-30 from 34 → 40
VIX points, covers Aug 2024, still does not cover Feb 2018 (0.89). **Is 0.95
with two documented exceptions acceptable, or do you want strict max, or an
explicit vol-shock add-on for the low-VIX regime?** (This interacts with Q1: both
misses are from a low base.)

## Q3. Calibration window — full sample or post-2012?

| window | beta_0 | −20% → CM-30 |
|---|---|---|
| 2004–now (default) | 192 | +34 |
| 2013–now | 227 | +37 |
| 2004–2012 | 251, k < 0 (saturating) | +20 |

The futures response roughly doubled after VIX ETPs appeared. The spec said
full sample with a stability check; the check shows the shift. Post-2012 is
more conservative and arguably the only regime that still exists. **Which
window?** One line in `config.py` (`FIT_START`).

## Q4. Scenarios beyond −30% are extrapolation

The envelope has data out to about −30% (2008, 2020). The grid now runs to
−40% at the owner's request (2008 was −40% over 49 days). Model at −40% from
today: CM-30 **93**, CM-120 **61**. The exponential form with `k = 0.9` is
nearly linear so this is not exploding, but nothing was observed there. **Is a
linear extrapolation beyond −30% acceptable, or should the grid be capped and
the tail handled by Q1(b)?**

## Q5. The up branch is an average fit, and it floors

Rallies use a least-squares (average) line, per spec. Two consequences:
- for a book that is long VIX calls, a rally is the loss scenario and an
  average fit is not conservative there;
- from CM-30 = 17.7, +20% gives 7.4 unfloored; the model floors at 9.0 and
  every scenario from +20% to +40% prints the same 9.0 at the front.

**Is that acceptable for this book, or should the up branch also be fitted to
the (lower) envelope, and should the floor be the historical minimum (≈ 9) or
something else?**

## Q6. Book inputs — LARGELY RESOLVED, one part remains

*Resolved since this was written.* The book now takes a **premium** per position and the
pricer backs the implied vol out of it with its own forward, day-count, rate and Black-76,
so base marks are exact by construction. Precedence is `premium` > `vol` > ATM curve, and
a new **`book_vols` gate FAILS the run** if any position falls back to the ATM curve.

Premium is preferred over an imported vol because it is the observable. Measured: a vol
struck against spot VIX rather than the future implies 133.80% where the correct figure is
126.92% — a **59% price error** when consumed. Passing the premium makes the convention
cancel.

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

---

## Q8. Curve coverage past the last tenor

CM tenors were extended from 120 to **150** on 2026-09-19. The listed curve supports it —
CM-150 is buildable on 99.9% of days, minimum reach ever 147 days — so the old 120 cap was
a spec choice, not a data limit.

**180 was tried and rejected by the model's own stress gate**: 3 understated episodes at
T180 against a threshold of 2, plus 71 gap days. An empirical boundary, not a judgement.

What remains: beyond the last tenor the curve **extrapolates flat and says nothing**. A
400-day option prices off the 150-day forward and the run exits 0. The VIX curve is
normally upward-sloping in contango, so flat understates the forward and underprices
long-dated calls; the shock is also read at that tenor, where `beta_T` has decayed well
past its fitted range.

No positions beyond 150 days exist today. **Should a position past the last tenor warn,
or fail a gate?** Our inclination is to fail, on the same reasoning as the `book_vols`
gate: silent is worse than loud.

---

# SPX EXTENSION — questions if we add SPX options

Full scope in `learning/SPX_EXTENSION_PLAN.md`; the data ask is written up separately in
`learning/VOL_SURFACE_DATA_REQUEST.md`.

The pricer itself is easy: SPX needs **no response function**, because the scenario *is*
the underlying move, and Black-76 already takes a forward. The hard parts are data, the
vol surface, and a missing contract multiplier.

## Q9. Can you provide the data, and given it, which model?

The framing we would rather use than arriving with a fixed design:

> **Can you provide the data needed for an SPX option pricer, and given what is actually
> available, which model should we use?**

What a pricer needs per option per day: strike, expiry, type (from the book), **premium
(mid)**, and a **forward per expiry**. We believe the desk feed has premiums, strikes,
time to expiry, a computed rate and vols. We do not believe it publishes a forward.

If it does not: a near-the-money **call and put at the same strike** per expiry lets us
imply the forward from put-call parity. That embeds dividends, borrow and financing
already, so no dividend forecast is needed and none can be wrong.

**Open:** is the feed full chains or held positions only? Is vol per-strike or
ATM-per-tenor? Per-strike full chains would mean the surface already exists.

## Q10. How should the vol surface move under an SPX shock?

The one genuinely hard modelling question. An SPX drop does not merely lower the index; it
lifts the whole surface and steepens the skew. Holding the surface fixed would materially
understate long-put P&L.

You build these surfaces and will have priors we do not. **Is a parallel level shift plus a
slope term adequate? Does the shift scale with the level, with the size of the move, with
tenor?**

This is the SPX analogue of our vol-of-vol layer — which is the weakest-fitted part of the
VIX model (30-day envelope R² 0.777 against 0.946 for the VIX response), so we are wary of
the effort involved.

## Q11. Two years of surface history — enough?

We may be able to get ~2 years. Measured against SPX over that window (502 trading days):

| pooled 1–20d windows | count |
|---|---|
| below −5% | 286 |
| below −10% | 41 |
| **below −15%** | **0** |
| **below −20%** | **0** |

Worst 20-day drawdown: **−12.1%** (April 2025).

That supports a shift rule for −5% to −12% on a real sample, and establishes all the
machinery. It does **not** support the −20% to −40% end of the grid, where a two-year fit
would extrapolate four-fold beyond its worst observation — and skew response is unlikely to
be linear, since the existence of put skew is the market saying the tail differs from the
body.

This is precisely the failure the VIX model documents at fixed horizons: fitted on 1-day
windows containing no −20% move, the convexity dial pinned at its bound and extrapolated to
+52 against a pooled answer of +34, confidently and without failing.

**Proposal: take the two years, and separately backfill ~60 days either side of Feb 2018,
Q4 2018, COVID, 2022, Aug 2024 and Apr 2025.** That is ~600 extra days rather than a decade,
and it is where the calibration information lives. If the existing fitter can be re-run over
historical chains, it is a bounded job rather than a purchase.

**Is that backfill feasible?** And if two years is all there ever is, we would state in the
output that the SPX layer is calibrated for moderate shocks and extrapolating beyond ~−12%.

## Q12. Joint P&L is scenario-consistent, not a correlation model

Combining both books asserts that at SPX −20% the index falls 20% **and** the VIX front
rises ~34 points, together, deterministically, with no dispersion.

That is the same single-factor assumption already in the VIX model (Q1), but it becomes
**load-bearing** once the two books are meant to hedge each other: the model will show the
hedge working perfectly in every scenario, by construction. Feb 2018 is the counter-example
— SPX −8% with VIX +17.5, which the model underestimates at a ratio of 0.72.

**Is scenario-consistent joint P&L acceptable for the intended use, or does the portfolio
measure need a genuine joint distribution?**

Note this also needs a **contract multiplier**, which does not exist anywhere in the code
today — P&L is currently index points × contracts, so VIX points and SPX points cannot be
summed at all.

---

## Things that are settled and not questions

- Down branch on the envelope, not least squares (why: understating is the failure mode).
- Built on futures, not spot (why: options settle on futures).
- Windows of 1–20 days pooled (why: −20% never happens in a day; fixed horizons dilute the tail).
- Vol-of-vol moved with the shock (why: holding it fixed understates).
- No live data dependency: history frozen on disk, daily rows from local CSVs, public CBOE files used only if reachable.
- Every run prints its own parameters, fit quality per branch, stress table, flags and gates.
