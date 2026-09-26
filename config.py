"""Every named parameter of the model lives here.  Nothing is buried in a fitting routine.

Plain-language summary of the choices below:
  * Tenors 30/60/90/120 days, interpolated linearly in calendar days.
  * Each branch of the response is fitted to the ENVELOPE that is conservative for
    the book's exposure, not to the average.  Down branch: the upper envelope, the
    biggest VIX rise seen for a drop of that size (DOWN_BRANCH_FIT).  Up branch: the
    lower envelope, the biggest VIX fall seen for a rally of that size
    (UP_BRANCH_FIT).  We calibrate to the bad tail because understating risk costs
    more than overstating it -- and which tail is "bad" depends on the book.
  * Back-month contracts that trade thinly are flagged and counted on every
    run; they stay in the fit by default because they were shown not to be
    stale (FIT_EXCLUDE_FLAGGED).
  * The shock is a move of X% over any window up to 20 trading days; the
    envelope is taken over all such windows (POOL_MAX_HORIZON).
  * The same formula serves VIX level (k > 0, accelerating) and vol-of-vol
    (k < 0, plateauing); the data picks the sign (K_BOUNDS).
"""
# Constant-maturity tenors, calendar days.  150 and 180 were added 2026-09-19: the listed
# curve supports them (CM-150 buildable on 99.9% of days, CM-180 on 98.7%, minimum reach
# ever 147 days), so the old 120 cap was a spec choice rather than a data limit.  Beyond
# the last tenor the Curve extrapolates FLAT, which understates an upward-sloping curve --
# so cover as much as the data honestly allows.  Back tenors are thinner: see README
# limitation 4 on 2008 liquidity at the long end.
TENORS = (30, 60, 90, 120, 150)
# SPX return scenarios, +-5..20% in 5% steps.  Trimmed from -40..+40 on 2026-09-26: the book is
# net long SPX puts, so the loss side is a rally, and beyond +20% every front-tenor answer is set
# by VIX_FLOOR rather than by the model.  The 14 stress episodes (step 5) still run to -40%.
SHOCKS = (-0.20, -0.15, -0.10, -0.05, 0.05, 0.10, 0.15, 0.20)

# --- constant-maturity construction (step 2)
CM_SPOT_ANCHOR = True     # when the front contract has > T days left, anchor the near end at (0, spot VIX)
MIN_VOLUME = 100          # contract-day fails liquidity if volume < this ...
MIN_OI = 1000             # ... or open interest < this

# --- response-function fit (step 4)
# The shock is "SPX moves X% over a window of up to POOL_MAX_HORIZON trading days".  The fit pools
# windows of every length 1..POOL_MAX_HORIZON and takes the envelope over the pool, so fast moves
# (which produce the largest VIX response) define the calibration.  A single fixed horizon dilutes
# the tail: windows ending after the VIX peak still carry a very negative SPX return.
POOL_MAX_HORIZON = 20
HORIZON_DAYS = 1          # single-horizon dataset used for diagnostics / horizon comparison only
DOWN_BRANCH_FIT = "envelope"   # "envelope" (upper envelope, conservative) | "lsq" (least squares, NOT for risk use)
ENVELOPE_QUANTILE = 0.95       # envelope = this quantile of dVIX within each SPX-return bin; 1.0 = strict max
# Up branch (rallies).  The book is net long SPX puts plus long and short VIX calls, so its
# loss scenario is a RALLY with vol collapsing -- the live tail is the LOWER one: the biggest
# VIX fall seen for a rally of that size.  If the book's direction ever flips (net short
# vol into a rally), the conservative tail flips too: set UP_ENVELOPE_QUANTILE = 0.95.
# The FORM stays linear whatever the estimator: only the points it is fitted through change.
# LIVE since the 2026-09-26 calibration (output/runs/20260926_135642_calibrate/), which moved
# exactly beta_up_0 68.3 -> 169.8 and lam_up 0.218 -> 0.277.  price.py warns if this setting
# and the frozen parameters ever disagree.
UP_BRANCH_FIT = "envelope"     # "envelope" (conservative for the book) | "lsq" (average; the pre-2026-09-26 fit)
UP_ENVELOPE_QUANTILE = 0.05    # 0.05 = lower envelope (VIX falls hardest).  LIVE TAIL: lower
K_BOUNDS = (-60.0, 5.0)        # k > 0 accelerating (VIX level), k < 0 saturating (vol-of-vol plateaus); the data picks
ENVELOPE_BIN_WIDTH = 0.01      # SPX-return bin width for the envelope, 1% bins
ENVELOPE_MIN_N = 5             # buckets with fewer windows than this are ignored (too noisy to define a worst case)
COVERAGE_MIN_DROP = 0.02       # coverage statistic counts windows with SPX return below -this
FIT_START = "2004-01-01"       # fit window; also run a post-2012 stability check
FIT_EXCLUDE_FLAGGED = False    # drop liquidity-flagged days from the fit.  Default False: a direct staleness
                               # test (settle unchanged while the front month moved > 1pt) fires on 0.15% of
                               # flagged contract-days -- CFE marks every contract daily -- and True would
                               # discard most of 2008 at the 120-day point.  Flags are still reported.
STALE_FRONT_MOVE = 1.0         # a contract is 'stale' if its settle is unchanged while the front moved > this
FIT_MIN_ABS_RETURN = 0.0       # ignore |r| below this in the fit (0 = use all days)

# Sane ranges -- a fitted value outside these triggers a loud warning (step 8)
# Set from the first full calibration (2026-09): beta_0 192, k 0.9, lam 0.20, beta_up_0 70, lam_up 0.23.
PARAM_RANGES = {
    "beta_0": (100.0, 400.0),  # VIX points per unit SPX return at the front (192 => -1% SPX -> +1.9 VIX)
    "k": (0.0, 5.0),           # downside convexity; above ~5 the -20% extrapolation explodes
    "lam": (0.05, 0.6),        # tenor damping per 30 days (0.20 => 120-day beta is 55% of the front)
    # Least squares gives ~68, the q=0.05 lower envelope ~170 (measured 2026-09-26 on the same
    # data).  The range admits both so flipping UP_BRANCH_FIT does not trip its own gate.
    "beta_up_0": (30.0, 300.0),
    "lam_up": (0.05, 0.6),
}

# --- gates (step 5 / step 8): the run prints FAIL if any of these is breached
CM_MIN_CORR = 0.90             # CM-30 vs spot VIX level correlation
CM_MIN_INVERSION_PCT = 50.0    # share of days with spot > 35 on which the curve is inverted
STRESS_MAX_UNDERSTATED = 2     # episodes per tenor the model may understate (known: Feb-2018, Aug-2024 at the front)
STRESS_MIN_RATIO = 0.5         # no episode may be understated by more than half

# --- vol-of-vol (step 6)
VOV_SOURCE = "auto"       # "historical" (frozen one-time file in data/bloomberg_historical/, NOT a live dependency)
                          # | "vvix" (free CBOE file, 30d only) | "auto" (historical if the file exists, else vvix)
# Set from the first full calibration (2026-09): gamma_0 1203, k -10.3, lam 0.18.  Units: vol points.
PARAM_RANGES_VOV = {
    "beta_0": (400.0, 3000.0),
    "k": (-40.0, 0.0),         # negative = saturating; a positive k here would mean vol-of-vol accelerates, which it does not
    "lam": (0.05, 0.6),
    "beta_up_0": (50.0, 800.0),
    "lam_up": (0.05, 1.0),
}

# --- option repricing (step 7)
RISK_FREE_RATE = 0.04

# Products the book may hold, keyed by the feed's `issuer_name`.  Anything else FAILS the book --
# there is no default.  The multiplier here is checked against the book's own `multiplier`
# column, so a new product (XSP, mini-VIX) cannot price at the wrong size: it has to be added here.
PRODUCTS = {
    "VOLATILITY INDEX (VIX)": {"underlying": "VIX", "multiplier": 100},
    "S&P 500 INDEX":          {"underlying": "SPX", "multiplier": 100},
}

# SPX forward = SPX close on the book date * exp((RISK_FREE_RATE - SPX_DIVIDEND_YIELD) * T).
# Flat carry, no futures pipeline: measured 2026-09-26, a 0.5% forward error moves rally P&L on a
# deep-OTM put by at most 2.3% (selloff side up to 7.8%), because the implied vol is inverted
# from the same forward and the error largely cancels.  A carry that is off by 0.5%/yr stays
# inside that test out to a year.  See README section on the SPX leg.
SPX_DIVIDEND_YIELD = 0.013
# SPX implied vol shock = SPX_VOL_SCALE * (the VIX response averaged over the futures spanning the
# option's life, tenors 0..T-30) / 100 -- portfolio.spx_vol_shock.  Reuses the VIX response (VIX is
# 30-day SPX implied vol).  1.0 is an ASSUMPTION for the reviewer to challenge: VIX is a
# variance-swap level and runs above ATM vol, so the true scale may be below 1.
SPX_VOL_SCALE = 1.0

# Floors.  Each one can set the answer in a scenario, so every binding is reported per scenario
# with the P&L it contributes -- none of these is allowed to act silently.
#   VIX_FLOOR: shocked VIX forward never below this.  Just under the lowest spot VIX close ever
#     (9.14, 2017-11-03).  Not 8.75: that was a contract's final settlement on its expiry morning
#     -- spot VIX's opening print, not a traded futures level.  With even one day left no VIX
#     future has settled below 9.88; with 30+ days, never below 11.32.  Confirmed 2026-09-26.
#   VOL_FLOOR_VIX: VIX-option vols run 70-130%; this has never bound.
#   VOL_FLOOR_SPX: PROPOSED, NEEDS REVIEW.  SPX ATM vol runs 10-13% in a calm tape; a rally shock
#     of -10 vol points would clamp at a 20% floor and truncate the vol collapse that is this
#     book's main loss.  5% is below any SPX implied vol on record (VIX low 9.14).
VIX_FLOOR = 9.0
VOL_FLOOR_VIX = 0.20
VOL_FLOOR_SPX = 0.05
DAILY_REFRESH = True      # try the public CBOE files each run (no key, no account). If the network is
                          # absent the run continues on data/daily_inputs/ + disk, so this is never a dependency.
MAX_CALIBRATION_AGE_DAYS = 400   # price.py FAILS if the fitted parameters are older than this.  Policy is a
                                 # YEARLY recalibration as a reviewed event (400 = a year plus slack for
                                 # scheduling); recalibrate sooner on a trigger -- see README section 0b.
# There is no data-age allowance: price.py prices AS OF THE DATE IN THE BOOK FILENAME and FAILS
# unless the market data has a row for exactly that date (gate curve_date).  Pricing Monday's
# marks off Friday's forwards passes silently otherwise -- the vol inverted from the premium
# absorbs the mismatch, the base mark still reproduces, and only the shocked number is wrong.
