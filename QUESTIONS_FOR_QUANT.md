# Questions for the quant review — 2026-09-21

Everything below is a decision the model owner cannot make alone. Each item
gives the number that raised it, where it comes from, and the choices. The
maths behind the numbers is in `output/report_<latest>.txt`; every figure here
is printed there.

The model itself validates well and is not the question: median model/realised
ratio 1.67 across 14 stress episodes, every 2008 leg covered at 1.6–4.6×, COVID
at 1.2×, 94.6% of all historical sell-offs beyond 2% at or below the model.

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

## Q6. Book inputs — one ATM vol per tenor is wrong for far-OTM calls

Pricing uses each option's own vol when the book supplies `vol` or `premium`.
Without it, the fallback is the ATM vol-of-vol curve, which marks far-OTM calls
near zero (a 90-day 100-strike call marked at ~0 vs ~4c market). The report now
lists every position on the fallback. **The owner can source vols or premiums —
confirm which the desk prefers, and whether skew should be held fixed through
the shock (current) or moved.**

## Q7. Vol-of-vol data provenance

The vol-of-vol layer was calibrated once on a frozen file of Bloomberg tenor
implied vols (`data/bloomberg_historical/`, documented there; nothing calls
Bloomberg). The alternative is free VVIX only, ~7% lower shocks, tenor fade
assumed. **Is a frozen one-time Bloomberg load acceptable for provenance, or
should it be VVIX-only?**

---

## Things that are settled and not questions

- Down branch on the envelope, not least squares (why: understating is the failure mode).
- Built on futures, not spot (why: options settle on futures).
- Windows of 1–20 days pooled (why: −20% never happens in a day; fixed horizons dilute the tail).
- Vol-of-vol moved with the shock (why: holding it fixed understates).
- No live data dependency: history frozen on disk, daily rows from local CSVs, public CBOE files used only if reachable.
- Every run prints its own parameters, fit quality per branch, stress table, flags and gates.
