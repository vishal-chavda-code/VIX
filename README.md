# SPX → VIX shock model

One question: **if SPX moves by X%, where does the VIX futures curve go, and what are
my VIX options worth then?**  Output feeds a portfolio risk measure.

```
python run.py                      # full pipeline + diagnostics, exit 0 only if every gate passes
python run.py --book book.csv      # reprice a real book  (columns: expiry, strike, type, quantity[, forward, vol])
python run.py --refresh            # re-download everything first
python run.py --asof 2020-03-16    # curves as of a historical date
```

Every run prints the full diagnostics report and writes it to `output/report_<time>.txt`.
Read section 8 (VERDICT) first.

---

## 1. The model in plain language

**VIX level.**  For an SPX move `r` and a tenor `T` (days), the change in the
constant-maturity VIX future is

```
dVIX_T(r) = beta_T · h(r)

    h(r) = -r                          r ≥ 0     rallies: VIX falls, in a straight line
    h(r) = (exp(-k·r) - 1) / k         r < 0     drops:  VIX rises, and accelerates (k > 0)

    beta_T = beta_0 · exp(-lam · T/30)           the front of the curve reacts most
```

`beta_T` is "VIX points per unit of SPX return for a small move" (192 at the front
means −1% SPX → +1.9 VIX).  `k` is the downside convexity.  `lam` is how fast
the response fades out the curve (0.20 means the 120-day point moves 55% as much
as the 30-day point).  The up and down branches have their own `beta` because
they are fitted differently (next section).

**Vol-of-vol.**  The same formula, applied to the implied vol of VIX options.
Here the data chooses `k < 0`, which makes the curve *plateau* instead of
accelerate: "VIX-option vol jumps on the first few percent of a sell-off and
then levels off."  It is the same one-line formula with the sign of `k` flipped.

**Repricing.**  Forward for each option = the VIX future for its expiry (book
mark, or the CM curve at that tenor).  Shocked forward = forward + `dVIX_T`.
Shocked vol = vol + `dSIGMA_T`.  Price with Black-76.  Both vol-fixed and
vol-shocked P&L are reported so the vol-of-vol effect is always visible.

## 2. The single most important decision: fit the down branch to the envelope

Understating risk is the failure mode.  A least-squares fit sits in the middle
of the cloud, so half of history produced a bigger VIX move than it predicts.
**The down branch is fitted to the upper envelope**: SPX returns are bucketed in
1% bins, the 95th percentile of the VIX response in each bin is taken, and the
curve is fitted through those points.

> "We calibrate to the worst observed response, not the typical one, because
> the cost of understating risk is higher than the cost of overstating it."

This is a named, switchable parameter: `config.DOWN_BRANCH_FIT` ("envelope" |
"lsq") and `config.ENVELOPE_QUANTILE` (0.95; 1.0 = strict maximum).  Every run
prints what least squares would have said next to what the envelope says:

| fit | −10% → CM-30 | −20% → CM-30 |
|---|---|---|
| envelope (used) | +16.4 | +34.4 |
| least squares (not used) | +7.8 | +18.8 |

## 3. What the data decided (and where it disagreed with the spec)

These were established by running each step and looking, not assumed.

1. **Windows are pooled over 1–20 trading days.**  At any single fixed horizon,
   the fit is unusable: 1-day windows never contain a −20% move, so the convex
   form extrapolates to +50 VIX or worse; 10-day windows that end *after* the
   VIX peak still carry a very negative SPX return and drag the envelope down
   to +23–26 for −20% when March 2020 actually delivered +30 to +44.  Pooling every
   window length and taking the envelope over the pool means: *the worst VIX
   response ever seen for a move of that size, however fast it happened.*
   (`config.POOL_MAX_HORIZON`).  The horizon comparison prints on every run.

2. **Downside convexity in futures is mild.**  `k ≈ 0.9`.  The violent
   acceleration people picture is in *spot* VIX; the 30-day future capped near
   70 in March 2020 while spot printed 82.  VIX options price off the future,
   so the model is built on futures.  The exp form still nests the linear case,
   so nothing is forced.

3. **Convexity fades with tenor** (free per-tenor `k`: 1.0 → 0.2 from 30 to 120
   days) but a single `k` loses nothing in fit quality (R² 0.94 either way), so
   the spec's single-`k` form is kept.

4. **Vol-of-vol saturates.**  A linear or convex fit understates the −5%
   vol-of-vol shock by half.  Allowing `k < 0` in the same formula fixes it
   (`config.K_BOUNDS`).

5. **Thin back-month contracts were not stale.**  A direct test (settle unchanged
   while the front moved > 1 point) fires on 0.15% of liquidity-flagged
   contract-days: CFE marks every contract daily.  So flagged days are reported
   but *not* excluded from the fit by default (`config.FIT_EXCLUDE_FLAGGED`);
   excluding them would discard most of 2008 at the 120-day point.

6. **Spot VIX anchors the near end of the curve** on the ~8 days a year when the
   front contract has more than 30 days left (`config.CM_SPOT_ANCHOR`).  A VIX
   future settles *to* VIX, so (tenor 0, spot) is a genuine point on the curve.
   Without it CM-30 has holes.  Anchored days are counted in the report.

## 4. Stress-episode validation (the gate)

Ratio = model / realised per tenor; < 1 means the model understated.

| episode | days | SPX | CM-30 real | model | ratio |
|---|---|---|---|---|---|
| 2008 Lehman → Oct-10 low | 20 | −28% | +20.3 | +50.2 | 2.5 |
| 2008 Lehman → Nov-20 low | 49 | −40% | +41.1 | +75.2 | 1.8 |
| 2011 Aug downgrade | 11 | −17% | +14.3 | +28.4 | 2.0 |
| 2015 Aug | 6 | −11% | +9.7 | +18.4 | 1.9 |
| **2018 Feb 5 (XIV day)** | 6 | −8% | **+17.5** | **+12.7** | **0.7** |
| 2020 COVID → Mar-16 | 18 | −29.5% | +43.7 | +53.0 | 1.2 |
| 2020 fast leg Mar 4–16 | 8 | −24% | +33.3 | +41.5 | 1.2 |
| **2024 Aug yen-carry** | 14 | −8.5% | **+17.1** | **+13.8** | **0.8** |
| 2025 Apr tariffs | 4 | −12% | +14.2 | +20.1 | 1.4 |

The model is calibrated to the worst response seen, which is March 2020; it
covers every 2008 leg with room to spare (2008 fell more slowly from an
already-elevated VIX).  **Two known exceptions at the 30/60-day points:**
Feb 5 2018 and Aug 5 2024 — modest SPX drops with enormous VIX spikes from a
very low base, both short-vol unwinds with a vol-market-structure component
that SPX does not explain.  Strict-max calibration (`ENVELOPE_QUANTILE = 1.0`)
lifts −20% at CM-30 from 34 → 40 and covers Aug 2024 but still not Feb 2018.
The gate allows up to `STRESS_MAX_UNDERSTATED = 2` such episodes per tenor and
no ratio below `STRESS_MIN_RATIO = 0.5`; it fails loudly otherwise.

## 5. Data

| series | source | notes |
|---|---|---|
| VIX futures 2004–2013 | `cdn.cboe.com/resources/futures/archive/volume-and-price/CFE_<M><YY>_VX.csv` | prices ×10 before 2007-03-26 (rescaled), some files carry a disclaimer line, a few have trailing commas |
| VIX futures 2013–now | `cdn.cboe.com/data/us/futures/market_statistics/historical_data/VX/VX_<expiry>.csv` | the 2013 files here are truncated with zero settles, so the archive is used through Dec-2013; expiry probed ±3 days around the rule for Good-Friday months |
| SPX | Yahoo `^GSPC` via yfinance (build machine) | on the work machine drop a `date,close` CSV at `data/raw/spx.csv` |
| spot VIX, VVIX | `cdn.cboe.com/api/global/us_indices/daily_prices/` | spot for the CM-30 validation and near-end anchor |
| VIX-option implied vol by tenor | Bloomberg: `VVIX Index` (30d), `VIX Index` `CALL_IMP_VOL_60D`, `3MO_CALL_IMP_VOL`, `6MO_CALL_IMP_VOL` | cached to `data/raw/bbg_vix_impvol.csv`; the `*_IMPVOL_100%MNY` and `CALL_IMP_VOL_30D` fields are unusable for VIX (moneyness measured against spot, not the future) — see `bloomberg.py` |

Monthly contracts only (weeklies exist from 2015 but not before, and are thin);
273 contract files, 47k contract-days, 2004-03-26 → today.  Only 3–4 contracts
were listed in 2004–05 and 2004–06 is heavily liquidity-flagged; the curve
always reaches at least 147 days so the 120-day point never has a gap.

## 6. Known limitations

1. **Mean reversion is not modelled.**  Far-dated futures are anchored to a
   long-run level; the multiplicative damping `exp(-lam·T/30)` approximates
   this.  The 120-day fit is as good as the 30-day fit (R² 0.94) so the
   approximation holds over the observed range, but it is not an anchor term.
2. **Single-factor shock.**  Only SPX drives the model.  The two understated
   stress episodes are exactly the cases with a vol-specific component.  Near
   zero the envelope shows a ±2 point noise floor that SPX does not explain.
3. **Regime instability.**  Fitted on 2004–2012 alone the envelope *saturates*
   (2008 was a slow grind from an elevated VIX): −20% → +20 at CM-30.  Fitted on
   2013–now it is linear-to-convex: −20% → +37.  The full sample (+34) is the
   default per spec; **the post-2012 calibration is the more conservative
   choice** and prints on every run with a warning.  Set `config.FIT_START =
   "2013-01-01"` to use it.
4. **Back-month data quality.**  71% of 2008's 120-day observations come from
   contracts trading under 100 lots a day.  They were not stale, but price
   discovery was thin; confidence at 120 days is lower than at 30.
5. **No starting-level dependence.**  The response is additive in VIX points
   regardless of where VIX starts.  The envelope is set by low-VIX starts
   (2018, 2020, 2024), so from an elevated base the model overstates.
6. **The up branch is a least-squares (average) fit**, per spec.  For a book
   that is long VIX calls, a rally is the loss scenario and an average fit is
   not conservative there.  The linear up branch also drives the front below
   any level VIX has traded at +15–20%; `shock.VIX_FLOOR` (9.0) catches it and
   the report says when it binds.
7. **Vol-of-vol methodology mix.**  30d is CBOE's VVIX, 60/90/180d are
   Bloomberg's ATM interpolation.  Levels are not comparable across the two;
   the fit uses changes, where the mismatch only mildly affects the tenor
   damping estimate.  180d has no Bloomberg history for 2009–2013.
8. **Stress windows longer than 20 days** (2008 → Nov-20, COVID → Mar-23) are
   outside the calibration pool; the model is applied and compared anyway and
   the table marks them.

## 7. Shipping to the work machine

The build is iterative; shipping is one-shot.  Carry the whole `vix_shock/`
folder including `data/raw/` (the CFE files, `spx.csv`, `vix.csv`, `vvix.csv`,
`bbg_vix_impvol.csv`).  With those present, `python run.py` needs no network
and no terminal.  `--refresh` re-pulls everything; Bloomberg refresh silently
keeps the cache if no terminal answers on 8194.

Checklist before the handoff review:

- [ ] `python run.py` exits 0 and section 8 reads ALL GATES PASS
- [ ] `output/step2_cm_vs_spot.png` — CM-30 hugs spot, term premium in calm, inversion in 2008 / 2020
- [ ] `output/step4_response_fit.png` — the black line tracks the red envelope points at every tenor
- [ ] `output/step6_vov_fit.png` — same, and the front plateaus
- [ ] the stress table's understated episodes are only the two documented ones
- [ ] `config.py` parameter ranges reflect the calibration you are shipping
- [ ] the real book runs through `--book` and the vol-of-vol effect column is non-trivial

Dependencies: pandas, numpy, scipy, matplotlib, requests.  `yfinance` only for
the SPX pull on the build machine; `blpapi` only for a Bloomberg refresh.

## 8. Layout

```
run.py                  entry point, writes output/report_<time>.txt, exit code = gates
config.py               every named parameter, sane ranges, gate thresholds
vixshock/
  data_sources.py       downloads (CFE two layouts, SPX, spot VIX, VVIX)
  ingest.py             step 1  contract files -> long table; ×10 rescale, placeholders, stale flag
  cm.py                 step 2  constant-maturity stitch; liquidity / stale / anchor / gap flags; spot validation
  join.py               step 3  SPX returns ⨝ CM changes; single-horizon and pooled 1-20d datasets
  response.py           step 4  the response function, envelope fit, per-tenor and joint fits, plots
  stress.py             step 5  stress-episode table
  bloomberg.py          blpapi adapter, cached
  volofvol.py           step 6  vol-of-vol fit (same machinery, k < 0 allowed)
  pricing.py            Black-76
  shock.py              step 7  shock grid, continuous curve, book repricing
  diagnostics.py        step 8  the report and the gates
data/raw                never modified      data/processed   rebuilt every run      output/   report, params, plots
```
