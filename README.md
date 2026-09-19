# SPX → VIX shock model

One question: **if SPX moves by X%, where does the VIX futures curve go, and what are
my VIX options worth then?**  Output feeds a portfolio risk measure.

```
python price.py --book input/book_2026-09-21.csv   # the DAILY run: price a book (~1s)
python run.py --book input/book_2026-09-21.csv      # YEARLY: recalibrate + full validation
python run.py --asof 2020-03-16    # curves as of a historical date
python bootstrap_history.py        # ONE-TIME on a machine with internet: loads the 2004-now history
python -m pytest tests/ -q         # 74 tests, ~8s.  Run before any commit.
```

`run.py` prints the full diagnostics report to `output/report_<time>.txt`; read section 8 (VERDICT)
first. Both entry points also write a self-contained `output/runs/<run-id>/` folder with a
`manifest.json` recording which calibration and market data produced the numbers. Exit code 0
only if every gate passed.
Open questions for the reviewer are in `QUESTIONS_FOR_QUANT.md`.

## 0. Where the data comes from

| | source | needs |
|---|---|---|
| History 2004 → now | `data/raw/`, loaded once by `bootstrap_history.py`, then frozen | internet once (build machine) |
| Today's rows, option A | CBOE public files + Yahoo SPX, pulled by `run.py` (or `price.py --refresh`) if `config.DAILY_REFRESH` and the network is there | **nothing** — no key, no account, no email; plain HTTPS |
| Today's rows, option B | `data/daily_inputs/` — three CSVs (VIX futures settles, SPX close, spot VIX) you or your feed maintain | nothing; see the README in that folder |
| Vol-of-vol calibration | `data/bloomberg_historical/` — one frozen file, pulled once; **not a dependency**, see the README there | nothing; delete it and the run falls back to free VVIX |
| Book | `input/book_<date>.csv`: `expiry, strike, type, quantity, premium` — full spec in `input/INPUT_CONTRACT.md` | market premiums (mid) from the desk |

Neither option A nor B is a *dependency*: with no network the run continues on B and the
disk; with nothing new at all the `data_fresh` gate **fails** once the curve is older than
`config.MAX_DATA_AGE_DAYS` (7). A run that fails is the intended behaviour on stale data.

Book file: **supply a `premium` for every position if you can** — the mid of the bid/ask
where available. Precedence is `premium` > `vol` > ATM curve. The premium is preferred
because it is the observable: inverting it here guarantees the vol is consistent with this
model's own forward, day-count, rate and Black-76, so the mark is exact by construction. An
imported `vol` carries whoever produced it's conventions — one struck against spot VIX
rather than the future runs ~7 vol points high at 90 days, a ~59% error in the price. Supply
both and the premium wins, with any gap over 1 vol point logged. Positions with neither fall
back to the ATM curve and are listed in the report — that fallback marks far-out-of-the-money
calls near zero, so do not ship a run with fallbacks on the real book.

Mid vs liquidation price: **mid is the more defensible input** for a shock model, because the
output is a *change* in value and mid carries no assumption about which way you would trade.
A bid/ask of 0.03/0.05 on a far-OTM call spans ~7 vol points, but only ±5% of the scenario
P&L, so use mid where you have it and do not block on it where you do not — record which you
used.

Scenario grid: SPX −40% to +40% in 5% steps (`config.SHOCKS`). Beyond −30% the model is
extrapolating past anything observed; see Q4 in `QUESTIONS_FOR_QUANT.md`.

---

## 0b. What is frozen and what is fresh

The model is **ten numbers**. Everything in `data/raw/` exists to produce them; once they
exist, pricing a book needs none of it. Keeping that line clear is what makes the model a
reviewable object rather than something that re-derives itself on every run.

| | what it is | refreshed |
|---|---|---|
| **Calibration inputs** | 22 years of SPX, VVIX and CM curve history | **yearly** (see below) |
| **The ten fitted numbers** | `output/response_params.json`, `output/vov_params.json` | only by a recalibration |
| **Pricing inputs** | today's VIX futures curve, spot VIX, vol-of-vol level | **every run** |
| **Book inputs** | strikes, expiries, quantities, premiums | every run |

The ten numbers: `beta_0`, `k`, `lam`, `beta_up_0`, `lam_up` for the VIX response, and the
same five for vol-of-vol.

**SPX is not a pricing input.** The SPX move is a *scenario you choose*, not something you
observe — `shock.py` reads no SPX data at all. SPX is used only by `join.py`, `stress.py` and
`volofvol.py`, which are calibration code. The same is true of the VVIX and Bloomberg
historical files. This is why the Yahoo SPX pull sits outside the daily path: it is touched
only during a recalibration, where a qualified vendor source can be substituted and frozen.

### Recalibration policy: yearly

Recalibrate **once a year**, or sooner on an explicit trigger. It is a reviewed event, never
a side effect of pricing a book.

Triggers for an off-cycle recalibration:

- the run prints the post-2012 drift warning and the gap has widened materially
- a stress episode occurs that the model would have understated — add it to `stress.EPISODES`
  and re-validate
- a structural change in the VIX futures market (new contract listings, a settlement change)

Each recalibration should produce, and keep: the full diagnostic report, the stress table, the
parameter files, and a note of the data vintage used.

**Why yearly rather than on every run.** Refitting as a side effect of pricing means the risk
number drifts for reasons unrelated to the book, two runs on the same book give different
answers, nothing records which calibration produced which number, and — the decisive one —
there is no fixed object for a validator to sign off on. The standard pattern is to calibrate
on a schedule with review, then apply frozen parameters until the next one.

### The two entry points

```
python run.py            CALIBRATE + validate + price.  Slow (minutes).
                         Refits the response function and vol-of-vol from the full
                         history, runs the 14 stress episodes, checks every gate,
                         writes output/response_params.json + vov_params.json.
                         Run YEARLY, or on a trigger, as a reviewed event.

python price.py --book b.csv     PRICE ONLY, against the frozen calibration.  ~1 second.
                         Loads the ten fitted numbers, applies them to today's curve,
                         never refits.  Run daily, or whenever a new book arrives.
```

`price.py` prints the **calibration in force** at the top of every run — the fit window,
observation count, method, quantile, the ten numbers, and when the parameter files were
written — so every priced output records which calibration produced it.

Its three gates:

| gate | fails when |
|---|---|
| `calibration_fresh` | the fitted parameters are older than `config.MAX_CALIBRATION_AGE_DAYS` (400) |
| `data_fresh` | the curve is older than `config.MAX_DATA_AGE_DAYS` (7) |
| `book_vols` | any position fell back to the ATM curve instead of its own premium/vol |

`book_vols` is the far-OTM protection: a book priced with ATM fallbacks marks
out-of-the-money calls near zero, so the run fails rather than quietly understating them.

Validation of the *calibration itself* — stress episodes, fit quality, parameter ranges —
lives only in `run.py`. `price.py` deliberately does not re-run it; it reports which
calibration it is using and leaves the validation to the run that produced it.

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
| VIX-option implied vol by tenor | one-time Bloomberg pull, frozen in `data/bloomberg_historical/` (README there lists the fields) | **not a dependency**; without the file the layer uses free VVIX (30d) with the tenor fade assumed |

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

The build is iterative; shipping is one-shot.  Carry the whole folder
including `data/raw/` (the CFE files, `spx.csv`, `vix.csv`, `vvix.csv`) and
`data/bloomberg_historical/`.  With those present, `python run.py` needs no
network and no terminal; daily rows come from the public CBOE files when the
network is there, else from `data/daily_inputs/`.

Checklist before the handoff review:

- [ ] `python run.py` exits 0 and section 8 reads ALL GATES PASS
- [ ] `output/step2_cm_vs_spot.png` — CM-30 hugs spot, term premium in calm, inversion in 2008 / 2020
- [ ] `output/step4_response_fit.png` — the black line tracks the red envelope points at every tenor
- [ ] `output/step6_vov_fit.png` — same, and the front plateaus
- [ ] the stress table's understated episodes are only the two documented ones
- [ ] `config.py` parameter ranges reflect the calibration you are shipping
- [ ] the real book runs through `--book` and the vol-of-vol effect column is non-trivial

Dependencies: pandas, numpy, scipy, matplotlib, requests.  `yfinance` only for
the SPX pull (bootstrap and optional daily refresh); nothing Bloomberg.

## 8. Layout

```
run.py                  CALIBRATE + validate + price.  YEARLY.  writes output/report_<time>.txt
price.py                PRICE ONLY vs the frozen calibration.  DAILY, ~1s.  writes output/pricing_<time>.txt
bootstrap_history.py    one-time history download (internet), never called by run.py
config.py               every named parameter, sane ranges, gate thresholds
QUESTIONS_FOR_QUANT.md  open decisions for the reviewer, with the numbers that raised them
vixshock/
  data_sources.py       history loaders + daily_inputs overlay; downloads (bootstrap / optional refresh)
  ingest.py             step 1  contract files -> long table; ×10 rescale, placeholders, stale flag
  cm.py                 step 2  constant-maturity stitch; liquidity / stale / anchor / gap flags; spot validation
  join.py               step 3  SPX returns ⨝ CM changes; single-horizon and pooled 1-20d datasets
  response.py           step 4  the response function, envelope fit, per-tenor and joint fits, plots
  stress.py             step 5  stress-episode table
  volofvol.py           step 6  vol-of-vol fit (same machinery, k < 0 allowed); reads data/bloomberg_historical/ or VVIX
  pricing.py            Black-76
  shock.py              step 7  shock grid, continuous curve, book repricing
  diagnostics.py        step 8  the report and the gates
data/raw                frozen history      data/daily_inputs   today's rows (local CSVs)     data/bloomberg_historical   frozen one-time file
data/processed          rebuilt every run   output/             report, params, plots
```
