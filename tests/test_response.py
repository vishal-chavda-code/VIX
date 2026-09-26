"""The response function: shape properties that must hold for any calibration.

These deliberately avoid asserting specific fitted values, which change legitimately
on recalibration. They assert the *structure* — which must not change.
"""
from __future__ import annotations

import numpy as np
import pytest

import config
from vixshock.response import beta_of_tenor, h_down


# ---------------------------------------------------------------- the kernel
def test_slope_is_minus_one_at_zero_for_any_k():
    """h has slope -1 at r=0 whatever k is.

    This is what makes beta interpretable as 'VIX points per unit SPX return for a
    small move'. If it breaks, every stated beta becomes meaningless.
    """
    eps = 1e-7
    for k in (-20.0, -1.0, 0.0, 0.9, 5.0):
        assert h_down(-eps, k) / eps == pytest.approx(1.0, abs=1e-4), f"k={k}"


def test_k_zero_is_the_straight_line():
    r = np.linspace(-0.4, 0, 50)
    assert np.allclose(h_down(r, 0.0), -r)


def test_k_near_zero_is_continuous():
    """No discontinuity at the k=0 branch in the code."""
    r = -0.2
    assert h_down(r, 1e-10) == pytest.approx(h_down(r, 0.0), abs=1e-6)


def test_positive_k_accelerates_negative_k_saturates():
    r = -0.30
    linear = -r
    assert h_down(r, 2.0) > linear      # accelerating
    assert h_down(r, -10.0) < linear    # saturating


def test_saturating_branch_has_the_stated_ceiling():
    """For k < 0 the asymptote is 1/|k|. Module 5 quotes ceilings from this."""
    k = -10.0
    assert h_down(-100.0, k) == pytest.approx(1 / abs(k), rel=1e-6)


def test_beta_of_tenor_decays():
    b = [beta_of_tenor(T, 200.0, 0.2) for T in (30, 60, 90, 120)]
    assert b == sorted(b, reverse=True)
    assert beta_of_tenor(0, 200.0, 0.2) == pytest.approx(200.0)


# ---------------------------------------------------------------- the fitted model
def test_down_moves_raise_vix_up_moves_lower_it(params):
    for T in config.TENORS:
        assert params.dvix(-0.10, T) > 0
        assert params.dvix(+0.10, T) < 0
        assert params.dvix(0.0, T) == pytest.approx(0.0, abs=1e-9)


def test_bigger_drop_bigger_response(params):
    for T in config.TENORS:
        vals = [float(params.dvix(r, T)) for r in (-0.05, -0.10, -0.20, -0.30)]
        assert vals == sorted(vals)


def test_front_reacts_more_than_the_back(params):
    """The tenor-fade dial: 30d must move more than 120d, both directions."""
    assert params.dvix(-0.20, 30) > params.dvix(-0.20, 120)
    assert params.dvix(+0.20, 30) < params.dvix(+0.20, 120)


def test_down_branch_is_harsher_than_up(params):
    """Leverage effect: a -20% drop moves VIX more than a +20% rally does."""
    assert abs(params.dvix(-0.20, 30)) > abs(params.dvix(+0.20, 30))


def test_vectorises_over_r_and_tenor(params):
    r = np.array([-0.2, -0.1, 0.0, 0.1])
    out = params.dvix(r, 30)
    assert out.shape == r.shape
    for i, x in enumerate(r):
        assert out[i] == pytest.approx(float(params.dvix(float(x), 30)))


def test_fitted_params_inside_sane_ranges(params):
    """The same check run.py gates on — caught here so a bad fit fails fast."""
    for name, (lo, hi) in config.PARAM_RANGES.items():
        v = getattr(params, name)
        assert lo <= v <= hi, f"{name}={v} outside [{lo}, {hi}]"


def test_vov_saturates(vov_params):
    """Vol-of-vol must have k < 0. A positive k would mean it accelerates, which the
    data has never supported and which would break the Module 5 explanation."""
    assert vov_params.k < 0
    for name, (lo, hi) in config.PARAM_RANGES_VOV.items():
        v = getattr(vov_params, name)
        assert lo <= v <= hi, f"{name}={v} outside [{lo}, {hi}]"


def test_calibration_is_not_stale_beyond_policy(params):
    """The fit window should reach recent data. Catches a params file left behind."""
    import pandas as pd
    assert pd.Timestamp(params.fit_end) > pd.Timestamp("2024-01-01")
    assert params.n_obs > 50_000


# ---------------------------------------------------------------- up-branch estimator (P0.1)
def _synthetic(beta_lo=150.0, beta_mid=60.0, n=40_000, seed=0):
    """Rallies whose VIX response has a known lower tail: y = -beta * r, beta drawn uniformly
    from an interval centred on beta_mid whose top is beta_lo."""
    rng = np.random.default_rng(seed)
    r = rng.uniform(0.001, 0.12, n)
    beta = rng.uniform(beta_mid - (beta_lo - beta_mid), beta_lo, n)    # centred on beta_mid, top at beta_lo
    return r, -beta * r


def test_up_envelope_recovers_the_lower_tail_slope():
    """Per bucket the 5th percentile of -beta*r is -(beta's 95th pct)*r, so the fit through the
    envelope must return beta's 95th percentile -- not its mean, which least squares returns."""
    from vixshock.response import _fit_up_one
    r, y = _synthetic()
    b_env, env = _fit_up_one(r, y, "envelope", 0.05)
    b_lsq, _ = _fit_up_one(r, y, "lsq")
    assert b_lsq == pytest.approx(60.0, rel=0.02)
    assert b_env == pytest.approx(60.0 + 0.9 * 90.0, rel=0.03)    # 95th pct of U(-30, 150) = 141
    assert len(env) > 5


def test_up_envelope_is_the_lower_tail_not_the_upper():
    from vixshock.response import envelope_points
    r, y = _synthetic()
    env = envelope_points(r, y, 0.05, side="up")
    assert (env["r_mean"] > 0).all()
    assert (env["y_env"] < env["y_mean"]).all()          # below the average: the bigger VIX fall


def test_up_branch_stays_linear_whatever_the_estimator(params):
    """The estimator changes the points, never the shape: dVIX(+2r) == 2 * dVIX(+r)."""
    for T in config.TENORS:
        assert params.dvix(0.20, T) == pytest.approx(2 * params.dvix(0.10, T), rel=1e-12)


def test_frozen_params_record_their_up_branch_estimator(params):
    assert params.method_up in ("lsq", "envelope")
    if params.method_up == "envelope":
        assert params.quantile_up is not None and params.envelope_points_up


@pytest.mark.parametrize("method_up,frozen", [
    ("lsq", "tests/regression/response_params_pre_change.json"),     # the calibration before 2026-09-26
    ("envelope", None),                                               # the calibration of record
])
def test_calibrations_reproduce_bit_for_bit(repo, pooled, method_up, frozen):
    """Refitting on the same data with the same estimator gives the same ten numbers exactly.
    Proves the up-branch change moved only the up branch, and that both calibrations are
    reproducible.  Skips only if the data on disk has moved past the calibrated window."""
    from vixshock.response import ResponseParams, calibration_of_record, fit_response
    target = ResponseParams.from_json(repo / frozen if frozen else
                                      calibration_of_record()[0] / "response_params.json")
    p = fit_response(pooled, method_up=method_up)
    if p.fit_end != target.fit_end:
        pytest.skip("data on disk has moved past the calibrated window")
    for k in ("beta_0", "k", "lam", "beta_up_0", "lam_up"):
        assert getattr(p, k) == getattr(target, k), k


def test_up_envelope_is_more_conservative_than_lsq_on_real_data(pooled):
    """On the real pool the q=0.05 envelope must imply a bigger VIX fall than least squares,
    and leave the down branch untouched."""
    from vixshock.response import fit_response
    pool = pooled
    env, lsq = fit_response(pool, method_up="envelope", q_up=0.05), fit_response(pool, method_up="lsq")
    assert env.beta_up_0 > lsq.beta_up_0
    assert (env.beta_0, env.k, env.lam) == (lsq.beta_0, lsq.k, lsq.lam)
    lo, hi = config.PARAM_RANGES["beta_up_0"]
    assert lo <= env.beta_up_0 <= hi and lo <= lsq.beta_up_0 <= hi   # neither trips its own gate
    assert env.per_tenor[30]["joint_coverage_up"] > 0.90 > lsq.per_tenor[30]["joint_coverage_up"]


def test_vol_of_vol_up_branch_is_pinned_to_lsq():
    """UP_BRANCH_FIT is scoped to the VIX-level response; the vol-of-vol fit must not follow it."""
    import inspect
    from vixshock import volofvol
    assert 'method_up="lsq"' in inspect.getsource(volofvol.fit_vov)


# ---------------------------------------------------------------- the calibration of record
def test_pricing_uses_the_committed_calibration_not_the_working_copy(repo):
    """output/response_params.json is gitignored: a fresh clone does not have it, and a machine
    that once ran run.py has its own.  Pricing must read the committed run folder instead."""
    import subprocess
    from vixshock.response import calibration_of_record
    folder, manifest = calibration_of_record()
    assert folder.parent == repo / "output" / "runs" and manifest["all_gates_pass"] is True
    tracked = subprocess.run(["git", "ls-files", "--error-unmatch", "output/runs/LATEST_CALIBRATION.txt"],
                             cwd=repo, capture_output=True).returncode == 0
    ignored = subprocess.run(["git", "check-ignore", "-q", "output/response_params.json"], cwd=repo).returncode == 0
    assert tracked and ignored


def test_a_failed_calibration_is_never_priced_with(tmp_path):
    from vixshock.response import calibration_of_record
    (tmp_path / "20990101_000000_calibrate").mkdir()
    (tmp_path / "LATEST_CALIBRATION.txt").write_text("20990101_000000_calibrate\nFAIL\n")
    with pytest.raises(RuntimeError, match="did not pass"):
        calibration_of_record(tmp_path)


def test_no_calibration_is_a_clear_error(tmp_path):
    from vixshock.response import calibration_of_record
    with pytest.raises(FileNotFoundError, match="LATEST_CALIBRATION"):
        calibration_of_record(tmp_path)
