# SPX → VIX shock model

One question: **if SPX moves by X%, where does the VIX futures curve go, and what is my
book of VIX and SPX options worth then?**  Output feeds a portfolio risk measure.

```
python price.py --book input/book_2026-09-18.csv   # the DAILY run: download the day's settles, price the book
python run.py --no-refresh                          # YEARLY: recalibrate + full validation, on the data on disk
python run.py --verify                              # CHECK this machine reproduces the committed calibration; changes nothing
python run.py --asof 2020-03-16    # curves as of a historical date
python bootstrap_history.py        # ONE-TIME on a machine with internet: loads the 2004-now history
python -m pytest tests/ -q         # 209 tests, ~40s.  Run before any commit.
```

**The book is net long SPX puts, plus long and short VIX calls. Its loss scenario is an SPX
rally with vol collapsing** — not a selloff. Every "conservative" choice below is conservative
*for that exposure*, and `config.py` names which tail is live so a change of strategy
direction cannot silently leave the conservatism pointing the wrong way.

`run.py` prints the full diagnostics report to `output/report_<time>.txt`; read section 8 (VERDICT)
first. Both entry points also write a self-contained `output/runs/<run-id>/` folder with a
`manifest.json` recording which calibration and market data produced the numbers. Exit code 0
only if every gate passed.
Open questions for the reviewer are in `QUESTIONS_FOR_QUANT.md`.

## 0. Where the data comes from

| | source | needs |
|---|---|---|
| History 2004 → now | `data/raw/`, loaded once by `bootstrap_history.py`, then frozen | internet once (build machine) |
| Today's rows, option A (**the default**) | CBOE public files (every listed VIX future, spot VIX, VVIX, SPX close), downloaded by `price.py` and `run.py` at the start of every run (~12s) | **nothing** — no key, no account, no email; plain HTTPS |
| Today's rows, option B (fallback) | `data/daily_inputs/` — three CSVs (VIX futures settles, SPX close, spot VIX) — only if the machine cannot reach CBOE | nothing; see the README in that folder |
| Vol-of-vol calibration | `data/bloomberg_historical/` — one frozen file, pulled once; **not a dependency**, see the README there | nothing; delete it and the run falls back to free VVIX |
| Book | `input/book_<YYYY-MM-DD>.csv`: `cusip, issuer_name, expiry, strike, type, quantity, multiplier, premium, src_bid, src_ask, src_close` (+ optional `premium_source`, `forward`) — full spec in `input/INPUT_CONTRACT.md` | positions and market premiums from the desk |

Neither option A nor B is a *dependency*: with no network the run continues on B and the
disk. **The pricing date is the date in the book's filename**, and the run fails (gate
`curve_date`) unless the VIX curve, spot VIX and — if the book holds SPX — the SPX close are
all from exactly that date. There is no staleness allowance: Monday's marks against Friday's
forwards reproduce the base price exactly and put the whole error in the shocked number.

Book file: **supply a `premium` for every position** — the mid of the bid/ask where
available. Forwards are not in the book: a VIX option's forward is that day's settle of the
VIX future expiring with it, looked up from the downloaded settles; an SPX option's is the
SPX close times a flat carry. The premium is preferred over any imported vol because it is the observable: inverting it here
guarantees the vol is consistent with this model's own forward, day-count, rate and Black-76,
so the mark is exact by construction. An imported vol carries whoever produced it's
conventions — one struck against spot VIX rather than the future runs ~7 vol points high at
90 days, a ~59% error in the price. A VIX position without a premium falls back to an ATM
curve (gate `book_vols` fails); one with no listed future on its expiry date (a weekly) and
no `forward` in the book falls back to the interpolated CM curve (gate `book_forwards` fails). An SPX position without a premium cannot be priced at
all and is rejected.

Mid vs liquidation price: **mid is the more defensible input** for a shock model, because the
output is a *change* in value and mid carries no assumption about which way you would trade.
A bid/ask of 0.03/0.05 on a far-OTM call spans ~7 vol points, but only ±5% of the scenario
P&L, so use mid where you have it and do not block on it where you do not — record which you
used.

Scenario grid: SPX ±5, 10, 15, 20% (`config.SHOCKS`), trimmed from −40..+40 on 2026-09-26.
The 14 stress episodes (section 4) still run out to −40%.

---

## 0b. What is frozen and what is fresh

The model is **ten numbers**. Everything in `data/raw/` exists to produce them; once they
exist, pricing a book needs none of it. Keeping that line clear is what makes the model a
reviewable object rather than something that re-derives itself on every run.

| | what it is | refreshed |
|---|---|---|
| **Calibration inputs** | 22 years of SPX, VVIX and CM curve history | **yearly** (see below) |
| **The ten fitted numbers** | the committed `output/runs/<run-id>_calibrate/` folder named by `output/runs/LATEST_CALIBRATION.txt` — the calibration of record | only by a recalibration that passes every gate |
| **Pricing inputs** | the VIX futures curve, spot VIX and SPX close on the book date | **every run** |
| **Book inputs** | strikes, expiries, quantities, premiums, VIX future settles | every run |

The ten numbers: `beta_0`, `k`, `lam`, `beta_up_0`, `lam_up` for the VIX response, and the
same five for vol-of-vol. The SPX leg adds **no** fitted numbers: it reuses the VIX response
(section 6b).

**The SPX *move* is not a pricing input; the SPX *level* is.** The move is a scenario you
choose, not something you observe. But an SPX option is priced off the index, so a book that
holds SPX options needs the SPX close on its pricing date — it is the base of every SPX
forward. A VIX-only book needs no SPX data to price. The SPX *history* is calibration input
only (`join.py`, `stress.py`, `volofvol.py`), as are the VVIX and Bloomberg historical files.

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

python price.py --book b.csv     PRICE ONLY, against the frozen calibration.  ~15 seconds,
                         most of it downloading the day's settles (--no-refresh: ~3s).
                         Loads the ten fitted numbers, applies them to today's curve,
                         never refits.  Run daily, or whenever a new book arrives.
```

`price.py` prints the **calibration in force** at the top of every run — the fit window,
observation count, method, quantile, the ten numbers, and when the parameter files were
written — so every priced output records which calibration produced it.

Its four gates:

| gate | fails when |
|---|---|
| `curve_date` | the VIX curve, spot VIX or (SPX books) the SPX close is not from the book's filename date. **Nothing is priced** |
| `calibration_fresh` | the fitted parameters are older than `config.MAX_CALIBRATION_AGE_DAYS` (400) |
| `book_vols` | any position fell back to the ATM curve instead of its own premium |
| `book_forwards` | any VIX position fell back to the interpolated CM curve instead of its own VIX future's settle |

`book_vols` is the far-OTM protection: a book priced with ATM fallbacks marks
out-of-the-money calls near zero, so the run fails rather than quietly understating them.
`book_forwards` and `curve_date` close the same kind of hole from the forward side: a wrong
forward is invisible in the base price (the vol absorbs it) and surfaces only in the shock.

It also prints a **FLOORS** table every run: for each scenario, how many positions each
floor bound, by how much, and the P&L the floor contributed (section 6b). And it warns if
`config.UP_BRANCH_FIT` differs from the estimator the frozen calibration was fitted with.

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

**Repricing** (`vixshock/portfolio.py`).  Each position is priced with Black-76 off its own
forward and the vol implied by its own premium, then again under each scenario:

|  | VIX option | SPX option |
|---|---|---|
| forward | that day's settle of the VIX future expiring with the option | SPX close × exp((rate − dividend yield) × T) |
| shocked forward | max(forward + `dVIX_T`, `VIX_FLOOR`) | forward × (1 + r) — the scenario *is* the move |
| vol shock | `dSIGMA_T` (vol-of-vol layer) | `SPX_VOL_SCALE` × `dVIX_T` / 100 |
| shocked vol | max(vol + shock, `VOL_FLOOR_VIX`) | max(vol + shock, `VOL_FLOOR_SPX`) |

P&L = (price − base price) × quantity × multiplier, **in currency**, reported per product
and for the whole book. Both vol-fixed and vol-shocked P&L are reported so the vol layer's
contribution (`vol_of_vol_effect`) is always visible and can be switched off in review.

## 2. The single most important decision: fit each branch to the envelope that hurts this book

Understating risk is the failure mode.  A least-squares fit sits in the middle
of the cloud, so half of history produced a bigger move than it predicts.
**Each branch is fitted to an envelope**: SPX returns are bucketed in 1% bins, a
quantile of the VIX response in each bin is taken, and the curve is fitted through
those points.  Which quantile depends on which tail is the book's loss:

| branch | fitted to | `config` | why |
|---|---|---|---|
| down (selloffs) | the **upper** envelope, q = 0.95: the biggest VIX rise seen for a drop that size | `DOWN_BRANCH_FIT`, `ENVELOPE_QUANTILE` | the conventional risk: VIX exploding |
| up (rallies) | the **lower** envelope, q = 0.05: the biggest VIX fall seen for a rally that size | `UP_BRANCH_FIT`, `UP_ENVELOPE_QUANTILE` | **this book's** risk: net long SPX puts and long VIX calls lose most when vol collapses into a rally |

> "Each branch is fitted to the envelope that is conservative for the book's exposure,
> because the cost of understating risk is higher than the cost of overstating it."

**The estimator changes; the shape does not.** The down branch stays convex, the up branch
stays a straight line — vol has a roughly steady beta to rallies with little convexity, and
that asymmetry is real. Only the points each line is fitted through change.

**If the book's direction flips**, so does the conservative tail: a book net short vol into a
rally needs `UP_ENVELOPE_QUANTILE = 0.95`. The config line says which tail is live.

Every calibration prints least squares next to the envelopes, both branches (measured
2026-09-26 on 2004-03-29 → 2026-09-18):

| fit | −10% → CM-30 | −20% → CM-30 | +10% → CM-30 | +20% → CM-30 | coverage of >2% moves, CM-30 |
|---|---|---|---|---|---|
| envelope, both branches | +16.6 | +34.6 | **−12.9** | **−25.8** | 94.9% down, 94.2% up |
| least squares, both branches | +7.7 | +18.5 | −5.5 | −11.0 | 51.8% down, 54.2% up |

**Status of the up branch: live since the 2026-09-26 calibration**
(`output/runs/20260926_135642_calibrate/`). Recalibrated on unchanged data, it moved exactly
two numbers — `beta_up_0` 68.3 → 169.8 and `lam_up` 0.218 → 0.277 — and every calibration
gate passed; the other eight are bit-identical (a test refits both calibrations and checks
this to the last digit). On the template book it deepens the rally loss by about a quarter:
−160k → −191k at +5%, −202k → −251k at +10%.

Two things the reviewer should know about it:

- **The up envelope fits less well than the down one.** R² on the envelope points is ~0.71
  at CM-30 (down branch: 0.95) and collapses at CM-150 (coverage 75%). The large-rally
  buckets are post-crisis rebounds from VIX 60–80.
- **It drives VIX_FLOOR hard.** At +20% the envelope says −25.8 points from a CM-30 of
  17.75; `VIX_FLOOR` catches it from +10% at the front. See section 6b and Q5.

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
lifts −20% at CM-30 from 34.6 → 41.7 and covers both (Feb 2018 ratio 1.22, Aug 2024
1.34; measured 2026-09-26).
The gate allows up to `STRESS_MAX_UNDERSTATED = 2` such episodes per tenor and
no ratio below `STRESS_MIN_RATIO = 0.5`; it fails loudly otherwise.

## 5. Data

| series | source | notes |
|---|---|---|
| VIX futures 2004–2013 | `cdn.cboe.com/resources/futures/archive/volume-and-price/CFE_<M><YY>_VX.csv` | prices ×10 before 2007-03-26 (rescaled), some files carry a disclaimer line, a few have trailing commas |
| VIX futures 2013–now | `cdn.cboe.com/data/us/futures/market_statistics/historical_data/VX/VX_<expiry>.csv` | the 2013 files here are truncated with zero settles, so the archive is used through Dec-2013; expiry probed ±3 days around the rule for Good-Friday months |
| SPX | history: Yahoo `^GSPC` via yfinance, once, at bootstrap. New days: CBOE's public `SPX_History.csv`, **appended** by the daily download | history is never rewritten: CBOE and Yahoo differ by up to ~8 points on 42 days in 2005–06, and a data refresh must not move the calibration's inputs |
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
6. **The up branch has no starting-level dependence either, and a floor catches it.**
   The linear up branch subtracts fixed points whatever VIX starts at, so at +15–20%
   it drives the front below any level VIX has printed — least squares from +20%,
   the lower envelope from +10%. `config.VIX_FLOOR` (9.0) catches it. The floor is a
   constant setting the answer in the loss scenario, so it is never silent: every
   binding is logged per position and scenario, and the P&L it contributes is
   reported (section 6b). Replacing it with a response that decays toward a floor
   is Q5 — it is the same problem as limitation 5, on the rally side.
7. **Vol-of-vol methodology mix.**  30d is CBOE's VVIX, 60/90/180d are
   Bloomberg's ATM interpolation.  Levels are not comparable across the two;
   the fit uses changes, where the mismatch only mildly affects the tenor
   damping estimate.  180d has no Bloomberg history for 2009–2013.
8. **Stress windows longer than 20 days** (2008 → Nov-20, COVID → Mar-23) are
   outside the calibration pool; the model is applied and compared anyway and
   the table marks them.
9. **The vol-of-vol up branch is still least squares.** `UP_BRANCH_FIT` governs the
   VIX-level response only (`volofvol.fit_vov` pins its up branch to `"lsq"`). Which
   tail is conservative for VIX-option vol in a rally depends on the long/short VIX
   call mix — an open question, not a default.

## 6b. The SPX leg and the floors: assumptions, with the numbers that justify them

The SPX leg was added 2026-09-26. It needs **no new calibration**.

**Forward: SPX close × a flat carry, no futures pipeline.** Measured before building
anything: price deep-OTM SPX puts (28–365 days, 70–95% moneyness, calm-tape skew) with
F = spot and again with F = spot × 1.005, inverting the vol from the same premium each time.

| scenario | worst P&L difference, as % of that put's P&L |
|---|---|
| +5% / +10% / +20% (the loss side) | 2.3% / 1.5% / 0.6% |
| −5% / −10% / −20% (the gain side) | 7.8% / 7.3% / 6.3% |

A forward error largely cancels, because the vol is inverted from the same forward; it
cancels best on the loss side, where a deep-OTM put heads to zero whatever the forward.
So F = SPX close × exp((`RISK_FREE_RATE` − `SPX_DIVIDEND_YIELD`) × T), with the yield a
documented config number (1.3%). A carry 0.5%/yr off stays inside this test out to a year.
Re-run it on a real position if the book grows long-dated. A book-supplied `forward` wins.

**Vol shock: reuse the VIX response.** VIX *is* 30-day SPX implied vol, so
`dSPX_vol(T, r) = SPX_VOL_SCALE × dVIX_T(r) / 100`, added to each position's own implied
vol. Base vols still come from each position's premium, so this applies a *change* to a
market-derived level. The assumptions, for the reviewer to challenge:

- **`SPX_VOL_SCALE` = 1.0.** VIX is a variance-swap level and runs above ATM vol, and VIX
  futures carry a convexity wedge. Both bite the *level*; only the *change* is used here,
  but the change may still scale below 1.
- **Tenor mapping: the average over the option's life** (switched 2026-09-26). A T-day
  option's vol covers days 0..T, which the VIX futures with tenors 0..T−30 tile, so the shock
  is the fitted response averaged over those tenors — exact for the exponential fade, same
  fitted numbers (`portfolio.spx_vol_shock`). The first build read the future *expiring* at T,
  which covers days T..T+30, after the option has gone, and understated the move 1.3× at one
  month, 1.8× at three, 2.9× at six. On the template's SPX puts the switch deepens the +5%
  loss from −162k to −179k; by +20% the puts are worthless either way. Tenor 0 is the futures
  fit extrapolated to spot, and spot VIX moves more than that in stress, so the front end is
  if anything still understated.
- **Sticky-strike plus a parallel shift, not sticky-moneyness.** The book carries one vol
  per position, not a surface, so the skew cannot move with moneyness. For long puts in a
  rally this is the conservative side: the put drifts further out, where the skew would
  have given it more vol.

**Floors.** Each can set the answer in a scenario, so each is reported, per scenario, with
the number of positions it bound, the largest lift, and `floor_effect` — the P&L with the
floor minus the P&L with it removed. Non-zero means a constant, not the model, set part of
the answer. Every binding is listed in the run's `floors.csv`.

| floor | value | why this value |
|---|---|---|
| `VIX_FLOOR` | 9.0 | just under the lowest spot VIX close ever (9.14, 2017-11-03). Not 8.75: that was a contract's final settlement on its expiry morning — spot's opening print, not a traded futures level. With a day or more left no VIX future has settled below 9.88; with 30+ days, never below 11.32 |
| `VOL_FLOOR_VIX` | 20% | VIX-option vols run 70–130%; has never bound |
| `VOL_FLOOR_SPX` | 5% | **proposed, needs review.** SPX ATM vol runs 10–13% in a calm tape; a 20% floor would clamp the rally vol collapse that is this book's main loss |

On the template book, with the envelope up branch live, `VIX_FLOOR` contributes +48k at +15%
and +89k at +20% — a large share of the loss-scenario answer. Kept at 9.0 deliberately
(2026-09-26); the alternative, a response that decays toward the floor, is folded into Q1.

## 7. Shipping to the work machine

The build is iterative; shipping is one-shot.  Carry the whole folder
including `data/raw/` (the CFE files, `spx.csv`, `vix.csv`, `vvix.csv`) and
`data/bloomberg_historical/`.  With those present, `python run.py` needs no
network and no terminal; daily rows come from the public CBOE files when the
network is there, else from `data/daily_inputs/`.

Checklist before the handoff review:

- [ ] `python run.py` exits 0 and section 8 reads ALL GATES PASS
- [ ] on the work machine, after pulling: `python run.py --verify` reads VERIFIED (the committed
      calibration reproduces there, bit for bit), then `python -m pytest tests/ -q`
- [ ] `output/step2_cm_vs_spot.png` — CM-30 hugs spot, term premium in calm, inversion in 2008 / 2020
- [ ] `output/step4_response_fit.png` — the black line tracks the red envelope points at every tenor
- [ ] `output/step6_vov_fit.png` — same, and the front plateaus
- [ ] the stress table's understated episodes are only the two documented ones
- [ ] `config.py` parameter ranges reflect the calibration you are shipping
- [ ] the real book runs through `price.py --book` with ALL GATES PASS, and the vol-of-vol effect column is non-trivial
- [ ] the FLOORS table on the real book: which scenarios have a non-zero `floor_effect`, and is the reviewer content with that
- [ ] `price.py` no longer warns that `UP_BRANCH_FIT` differs from the frozen calibration (or the reviewer has chosen to keep least squares)

Dependencies: pandas, numpy, scipy, matplotlib, requests.  `yfinance` only for
the one-time SPX history pull (bootstrap); the daily path is CBOE only.  Nothing Bloomberg.

## 8. Layout

```
run.py                  CALIBRATE + validate + price.  YEARLY.  output/report_<time>.txt
price.py                PRICE ONLY vs the frozen calibration.  DAILY, ~15s with the download.
bootstrap_history.py    one-time history download (needs internet); never called by the others
config.py               every named parameter, sane ranges, gate thresholds
QUESTIONS_FOR_QUANT.md  open decisions for the reviewer, with the numbers that raised them

vixshock/
  data_sources.py       history loaders + daily_inputs overlay; bootstrap / optional refresh
  ingest.py             step 1  contract files -> long table; x10 rescale, placeholders, stale flag
  cm.py                 step 2  constant-maturity stitch; liquidity / stale / anchor / gap flags
  join.py               step 3  SPX returns joined to CM changes; pooled 1-20d windows
  response.py           step 4  the response function, envelope fit, per-tenor and joint fits
  stress.py             step 5  stress-episode table
  volofvol.py           step 6  vol-of-vol fit (same machinery, k < 0 allowed)
  pricing.py            Black-76 and the implied-vol inverter
  validate.py           book-input validation: schema, products, cusip key, ISO dates, the filename date
  shock.py              step 7a the market as of ONE date (and which series are not), the shocked curve
  portfolio.py          step 7b THE PRICING LAYER: VIX + SPX options, floors, currency P&L, subtotals
  diagnostics.py        step 8  the report and the gates

tests/                  209 tests, ~40s.  python -m pytest tests/ -q
  regression/           the pre-2026-09-26 pricer's output on a VIX-only book: the new layer must match x100
input/                  book files in.  INPUT_CONTRACT.md is the full spec for the feed builder

data/raw                frozen history           data/daily_inputs        today's rows (local CSVs)
data/bloomberg_historical  frozen one-time file  data/processed           rebuilt by run.py
output/                 report_<t>.txt + params;  runs/<run-id>/  one folder per run, with a manifest
```

## 9. Documentation map

| | |
|---|---|
| `input/INPUT_CONTRACT.md` | book-file spec: columns, ISO dates, premium over vol |
| `data/daily_inputs/README.md` | market-data formats, if the machine cannot reach CBOE |
| `output/runs/README.md` | what a run folder contains, how to read a manifest |

### Known open items

- **No starting-level dependence** — the model adds fixed VIX points regardless of where
  VIX starts. The main open question; Q1 for the reviewer. On the rally side it is what
  makes `VIX_FLOOR` bind (Q5).
- **SPX vol shock scale** — `SPX_VOL_SCALE` = 1.0 is an assumption (Q13). The tenor mapping
  was switched to the average over the option's life on 2026-09-26 (Q14, settled).
- **Beyond the last tenor** — the VIX *forward* is now the listed future's own settle, so a long-dated
  VIX option is no longer priced off the flat-extrapolated curve. The *shock* is still read
  past the last calibrated tenor (150d) for such positions; they are flagged in every run.
- ~~No contract multiplier~~ — done 2026-09-26: P&L is in currency, per product and total.
