"""Every named parameter of the model lives here.  Nothing is buried in a fitting routine.

Plain-language summary of the choices below:
  * Tenors 30/60/90/120 days, interpolated linearly in calendar days.
  * Down-move branch is fitted to the UPPER ENVELOPE of observed responses,
    not the average (DOWN_BRANCH_FIT).  We calibrate to the worst observed
    response because understating risk costs more than overstating it.
  * Back-month contracts that trade thinly are flagged and counted on every
    run; they stay in the fit by default because they were shown not to be
    stale (FIT_EXCLUDE_FLAGGED).
  * The shock is a move of X% over any window up to 20 trading days; the
    envelope is taken over all such windows (POOL_MAX_HORIZON).
  * The same formula serves VIX level (k > 0, accelerating) and vol-of-vol
    (k < 0, plateauing); the data picks the sign (K_BOUNDS).
"""
TENORS = (30, 60, 90, 120)            # constant-maturity tenors, calendar days
# SPX return scenarios.  The spec grid is +-5..20%; -30% and -40% are added because the book must
# hold up in the ugliest markets seen (2020: -34% in 23 days; 2008: -40% in 49 days).  Beyond -20%
# the model is extrapolating past its calibration envelope (which ends near -30%) -- see the
# open question on starting-level dependence in QUESTIONS_FOR_QUANT.md.
SHOCKS = tuple(x / 100 for x in range(-40, 0, 5)) + tuple(x / 100 for x in range(5, 45, 5))   # -40..+40 in 5% steps

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
    "beta_up_0": (30.0, 150.0),
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
DAILY_REFRESH = True      # try the public CBOE / Yahoo files each run (no key, no account). If the network is
                          # absent the run continues on data/daily_inputs/ + disk, so this is never a dependency.
MAX_DATA_AGE_DAYS = 7     # FAIL the run if the latest curve date is older than this (calendar days) when
                          # pricing as of today.  Raise it deliberately if the machine cannot refresh data.
