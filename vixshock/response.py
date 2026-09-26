"""Step 4 -- the SPX -> dVIX response function and its fit.

    dVIX_T(r) = beta_T * h(r)                  r = SPX return, T = tenor in days

        h(r) = -r                              r >= 0   (linear: VIX falls on rallies)
        h(r) = (exp(-k r) - 1) / k             r <  0   (convex: VIX rises, and accelerates, on drops)

        beta_T = beta_0 * exp(-lam * T / 30)   (tenor damping: front reacts most)

    h is continuous with slope -1 at zero, so beta_T is "VIX points per unit SPX
    return for small moves"; k is the downside convexity.  k > 0 accelerates,
    k < 0 saturates at 1/|k| (vol-of-vol does this), k = 0 is a straight line;
    the fit picks the sign within config.K_BOUNDS.  The two branches get their
    own beta so they can be fitted differently:

      * down branch -> envelope at ENVELOPE_QUANTILE     (config.DOWN_BRANCH_FIT)
      * up branch   -> envelope at UP_ENVELOPE_QUANTILE  (config.UP_BRANCH_FIT)

    Envelope: bin SPX returns in 1% buckets, take a quantile of dVIX in each
    bucket, fit the curve through those points.  Each branch is fitted to the
    tail that is conservative FOR THE BOOK'S EXPOSURE: 0.95 on the down branch
    (the largest VIX rise seen for a drop of that size), 0.05 on the up branch
    (the largest VIX fall seen for a rally of that size -- the loss for a book
    that is long vol into a rally).  The shape does not change with the
    estimator: the up branch stays a straight line, the down branch convex.
"""
from __future__ import annotations

import dataclasses
import json
import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy.optimize import least_squares

import config
from .data_sources import ROOT

log = logging.getLogger(__name__)


# ---------------------------------------------------------------- the function
def h_down(r: np.ndarray, k: float) -> np.ndarray:
    """(exp(-k r) - 1) / k.  k > 0: accelerates as r falls; k < 0: saturates at
    1/|k|; k -> 0: the straight line -r.  Slope -1 at r = 0 for every k."""
    r = np.asarray(r, dtype=float)
    if abs(k) < 1e-9:
        return -r
    return np.expm1(-k * r) / k


def beta_of_tenor(T, beta_0: float, lam: float):
    return beta_0 * np.exp(-lam * np.asarray(T, dtype=float) / 30.0)


@dataclass
class ResponseParams:
    beta_0: float          # down-branch front sensitivity (VIX pts per unit return, small moves)
    k: float               # downside convexity
    lam: float             # down-branch tenor damping per 30 days
    beta_up_0: float       # up-branch front sensitivity
    lam_up: float          # up-branch tenor damping
    method_down: str
    quantile: float
    horizon: str           # "pooled 1-20" or a single number of days
    fit_start: str
    fit_end: str
    n_obs: int
    per_tenor: dict = field(default_factory=dict)   # unconstrained per-tenor fits, for validation
    envelope_points: dict = field(default_factory=dict)
    # Up-branch estimator.  The defaults describe calibrations written before 2026-09-26,
    # which always fitted the up branch by least squares and did not record it.
    method_up: str = "lsq"
    quantile_up: float | None = None
    envelope_points_up: dict = field(default_factory=dict)

    def dvix(self, r, T):
        """Shocked change in the CM-T VIX future for SPX return r (either may be an array)."""
        r = np.asarray(r, dtype=float)
        down = beta_of_tenor(T, self.beta_0, self.lam) * h_down(np.minimum(r, 0.0), self.k)
        up = beta_of_tenor(T, self.beta_up_0, self.lam_up) * (-np.maximum(r, 0.0))
        return down + up

    def to_json(self, path):
        d = dataclasses.asdict(self)
        d["per_tenor"] = {str(k): v for k, v in d["per_tenor"].items()}
        d["envelope_points"] = {str(k): v for k, v in d["envelope_points"].items()}
        d["envelope_points_up"] = {str(k): v for k, v in d["envelope_points_up"].items()}
        with open(path, "w") as f:
            json.dump(d, f, indent=2, default=float)

    @classmethod
    def from_json(cls, path):
        with open(path) as f:
            d = json.load(f)
        d["per_tenor"] = {int(k): v for k, v in d["per_tenor"].items()}
        d["envelope_points"] = {int(k): v for k, v in d["envelope_points"].items()}
        d["envelope_points_up"] = {int(k): v for k, v in d.get("envelope_points_up", {}).items()}
        return cls(**d)

    def summary(self) -> str:
        up = self.method_up + (f" q={self.quantile_up}" if self.method_up == "envelope" else "")
        lines = [f"response function  (down: {self.method_down} q={self.quantile}; up: {up}; "
                 f"horizon {self.horizon}d; "
                 f"fit {self.fit_start} -> {self.fit_end}, n={self.n_obs})",
                 f"  beta_0 = {self.beta_0:8.2f}   k = {self.k:6.2f}   lam = {self.lam:5.3f}      (down branch)",
                 f"  beta_up_0 = {self.beta_up_0:5.2f}              lam_up = {self.lam_up:5.3f}   (up branch)",
                 "  implied beta_T:  " + "  ".join(f"T{T}: dn {beta_of_tenor(T, self.beta_0, self.lam):6.1f} / up "
                                                   f"{beta_of_tenor(T, self.beta_up_0, self.lam_up):5.1f}"
                                                   for T in config.TENORS)]
        return "\n".join(lines)


# ---------------------------------------------------------------- envelope points
def envelope_points(r: np.ndarray, y: np.ndarray, q: float = config.ENVELOPE_QUANTILE,
                    width: float = config.ENVELOPE_BIN_WIDTH, min_n: int = config.ENVELOPE_MIN_N,
                    side: str = "down") -> pd.DataFrame:
    """Envelope of the (r, y) cloud on one side of zero: per bucket of width `width`,
    the q-quantile of y and the mean r.  Buckets with < min_n points are skipped.

    side="down" uses r < 0 (q = 0.95 is the upper envelope: the biggest VIX rises);
    side="up" uses r > 0 (q = 0.05 is the lower envelope: the biggest VIX falls)."""
    r, y = np.asarray(r), np.asarray(y)
    m = r < 0 if side == "down" else r > 0
    r, y = r[m], y[m]
    if side == "down":
        edges = np.arange(0.0, r.min() - width, -width)[::-1]
    else:
        edges = np.arange(0.0, r.max() + width, width)
    idx = np.digitize(r, edges) - 1
    rows = []
    for b in np.unique(idx):
        sel = idx == b
        if sel.sum() < min_n:
            continue
        rows.append({"bin_lo": edges[b], "bin_hi": edges[b + 1] if b + 1 < len(edges) else 0.0,
                     "n": int(sel.sum()), "r_mean": float(r[sel].mean()),
                     "y_env": float(np.quantile(y[sel], q)), "y_max": float(y[sel].max()),
                     "y_min": float(y[sel].min()), "y_mean": float(y[sel].mean())})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- fitting
def _fit_down_one(r, y, method: str, q: float) -> tuple[float, float, pd.DataFrame]:
    """Single-tenor down-branch fit -> (beta, k, envelope table)."""
    env = envelope_points(r, y, q)
    if method == "envelope":
        x, t = env["r_mean"].to_numpy(), env["y_env"].to_numpy()
    elif method == "lsq":
        m = r < 0
        x, t = np.asarray(r)[m], np.asarray(y)[m]
    else:
        raise ValueError(f"unknown DOWN_BRANCH_FIT {method!r}")
    res = least_squares(lambda p: p[0] * h_down(x, p[1]) - t, x0=[100.0, 0.5],
                        bounds=([0.0, config.K_BOUNDS[0]], [5000.0, config.K_BOUNDS[1]]))
    return float(res.x[0]), float(res.x[1]), env


def _fit_up_one(r, y, method: str = "lsq", q: float | None = None) -> tuple[float, pd.DataFrame]:
    """Single-tenor up-branch fit through the origin, y = -beta * r on r > 0 -> (beta, envelope).

    lsq      -> every rally window: the average response
    envelope -> the q-quantile per 1% bucket (q = 0.05: the largest VIX fall seen)
    The form is linear either way; only the points it is fitted through change."""
    if method == "envelope":
        env = envelope_points(r, y, q, side="up")
        x, t = -env["r_mean"].to_numpy(), env["y_env"].to_numpy()
    elif method == "lsq":
        env = pd.DataFrame()
        m = r > 0
        x, t = -np.asarray(r)[m], np.asarray(y)[m]
    else:
        raise ValueError(f"unknown UP_BRANCH_FIT {method!r}")
    return float((x * t).sum() / (x * x).sum()), env


def _r2(y, yhat) -> float:
    y, yhat = np.asarray(y), np.asarray(yhat)
    ss = ((y - y.mean()) ** 2).sum()
    return float(1 - ((y - yhat) ** 2).sum() / ss) if ss > 0 else float("nan")


def fit_response(ds: pd.DataFrame, tenors=config.TENORS, method: str = config.DOWN_BRANCH_FIT,
                 q: float = config.ENVELOPE_QUANTILE, horizon: str = f"pooled 1-{config.POOL_MAX_HORIZON}",
                 fit_start: str = config.FIT_START, fit_end: str | None = None,
                 exclude_flagged: bool = config.FIT_EXCLUDE_FLAGGED,
                 method_up: str = config.UP_BRANCH_FIT,
                 q_up: float = config.UP_ENVELOPE_QUANTILE) -> ResponseParams:
    d = ds.loc[fit_start:fit_end]
    if exclude_flagged:
        d = d[~d["flag"]]
    if config.FIT_MIN_ABS_RETURN > 0:
        d = d[d["spx_ret"].abs() >= config.FIT_MIN_ABS_RETURN]
    r = d["spx_ret"].to_numpy()

    # 1. unconstrained per-tenor fits (validation of the functional form)
    per_tenor, envs, envs_up = {}, {}, {}
    for T in tenors:
        y = d[f"dvix_{T}"].to_numpy()
        b_dn, k_T, env = _fit_down_one(r, y, method, q)
        b_up, env_up = _fit_up_one(r, y, method_up, q_up)
        m_dn, m_up = r < 0, r > 0
        m_cov = r < -config.COVERAGE_MIN_DROP
        yhat_dn = b_dn * h_down(r[m_dn], k_T)
        per_tenor[T] = {
            "beta_dn": b_dn, "k": k_T, "beta_up": b_up,
            "r2_env_points": _r2(env["y_env"], b_dn * h_down(env["r_mean"], k_T)),
            # share of windows with a drop beyond COVERAGE_MIN_DROP whose dVIX is at or below the model
            "coverage_down": float((y[m_cov] <= b_dn * h_down(r[m_cov], k_T)).mean()),
            # R2 against what the branch was fitted to: the whole rally cloud for least squares,
            # the envelope points for an envelope (an envelope line is not meant to fit the middle).
            "r2_up": (_r2(env_up["y_env"], -b_up * env_up["r_mean"]) if method_up == "envelope"
                      else _r2(y[m_up], -b_up * r[m_up])),
            "n_down": int(m_dn.sum()), "n_up": int(m_up.sum()),
        }
        envs[T] = env.round(5).to_dict("records")
        envs_up[T] = env_up.round(5).to_dict("records")

    # 2. joint constrained fit: beta_T = beta_0 exp(-lam T/30), one k across tenors
    def resid_down(p):
        out = []
        for T in tenors:
            env = pd.DataFrame(envs[T])
            if method == "envelope":
                x, t = env["r_mean"].to_numpy(), env["y_env"].to_numpy()
            else:
                m = r < 0
                x, t = r[m], d[f"dvix_{T}"].to_numpy()[m]
            out.append(beta_of_tenor(T, p[0], p[2]) * h_down(x, p[1]) - t)
        return np.concatenate(out)

    p0 = [per_tenor[tenors[0]]["beta_dn"], np.median([v["k"] for v in per_tenor.values()]), 0.3]
    res = least_squares(resid_down, x0=p0,
                        bounds=([0.0, config.K_BOUNDS[0], 0.0], [5000.0, config.K_BOUNDS[1], 10.0]))
    beta_0, k, lam = map(float, res.x)

    def resid_up(p):
        out = []
        for T in tenors:
            if method_up == "envelope":
                env = pd.DataFrame(envs_up[T])
                x, t = env["r_mean"].to_numpy(), env["y_env"].to_numpy()
            else:
                m = r > 0
                x, t = r[m], d[f"dvix_{T}"].to_numpy()[m]
            out.append(-beta_of_tenor(T, p[0], p[1]) * x - t)
        return np.concatenate(out)

    res_up = least_squares(resid_up, x0=[per_tenor[tenors[0]]["beta_up"], 0.3],
                           bounds=([0.0, 0.0], [5000.0, 10.0]))
    beta_up_0, lam_up = map(float, res_up.x)

    params = ResponseParams(beta_0=beta_0, k=k, lam=lam, beta_up_0=beta_up_0, lam_up=lam_up,
                            method_down=method, quantile=q, horizon=horizon,
                            fit_start=str(d.index.min().date()), fit_end=str(d.index.max().date()),
                            n_obs=len(d), per_tenor=per_tenor, envelope_points=envs,
                            method_up=method_up, quantile_up=q_up if method_up == "envelope" else None,
                            envelope_points_up=envs_up if method_up == "envelope" else {})
    # joint-model fit quality per tenor, per branch
    for T in tenors:
        y = d[f"dvix_{T}"].to_numpy()
        m_cov, m_up = r < -config.COVERAGE_MIN_DROP, r > 0
        m_cov_up = r > config.COVERAGE_MIN_DROP
        yhat = params.dvix(r, T)
        env = pd.DataFrame(envs[T])
        per_tenor[T]["joint_r2_env_points"] = _r2(env["y_env"], params.dvix(env["r_mean"].to_numpy(), T))
        per_tenor[T]["joint_coverage_down"] = float((y[m_cov] <= yhat[m_cov]).mean())
        if method_up == "envelope":
            eu = pd.DataFrame(envs_up[T])
            per_tenor[T]["joint_r2_up"] = _r2(eu["y_env"], params.dvix(eu["r_mean"].to_numpy(), T))
        else:
            per_tenor[T]["joint_r2_up"] = _r2(y[m_up], yhat[m_up])
        # Mirror of coverage_down: share of rallies beyond COVERAGE_MIN_DROP whose VIX fall was
        # no bigger than the model's.  About 50% for least squares, about 95% for a q=0.05 envelope.
        per_tenor[T]["joint_coverage_up"] = float((y[m_cov_up] >= yhat[m_cov_up]).mean())
        per_tenor[T]["joint_beta_dn"] = float(beta_of_tenor(T, beta_0, lam))
        per_tenor[T]["joint_beta_up"] = float(beta_of_tenor(T, beta_up_0, lam_up))
    return params


def fit_table(params: ResponseParams) -> pd.DataFrame:
    """Per-tenor, per-branch fit quality: unconstrained vs joint form."""
    rows = []
    for T, v in params.per_tenor.items():
        rows.append({"tenor": T,
                     "beta_dn (free)": v["beta_dn"], "k (free)": v["k"], "beta_dn (joint)": v["joint_beta_dn"],
                     "R2 env pts (free)": v["r2_env_points"], "R2 env pts (joint)": v["joint_r2_env_points"],
                     "coverage dn (joint)": v["joint_coverage_down"],
                     "beta_up (free)": v["beta_up"], "beta_up (joint)": v["joint_beta_up"],
                     "R2 up (free)": v["r2_up"], "R2 up (joint)": v["joint_r2_up"],
                     "coverage up (joint)": v.get("joint_coverage_up", float("nan")),
                     "n_dn": v["n_down"], "n_up": v["n_up"]})
    return pd.DataFrame(rows).set_index("tenor").round(3)


def plot_fit(ds: pd.DataFrame, params: ResponseParams, out_png, tenors=config.TENORS):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    d = ds.loc[params.fit_start:params.fit_end]
    r = d["spx_ret"].to_numpy()
    grid = np.linspace(min(r.min(), -0.20), max(r.max(), 0.20), 400)
    ncol = int(np.ceil(len(tenors) / 2))
    fig, axes = plt.subplots(2, ncol, figsize=(7 * ncol, 10))
    for ax in axes.ravel()[len(tenors):]:
        ax.set_visible(False)
    for ax, T in zip(axes.ravel(), tenors):
        y = d[f"dvix_{T}"].to_numpy()
        ax.scatter(r, y, s=4, alpha=0.3, color="grey", label="observed days")
        env = pd.DataFrame(params.envelope_points[T])
        ax.scatter(env["r_mean"], env["y_env"], s=30, color="red", zorder=5, label=f"envelope (q={params.quantile})")
        if params.envelope_points_up.get(T):
            eu = pd.DataFrame(params.envelope_points_up[T])
            ax.scatter(eu["r_mean"], eu["y_env"], s=30, color="blue", zorder=5,
                       label=f"up envelope (q={params.quantile_up})")
        ax.plot(grid, params.dvix(grid, T), color="black", lw=1.5, label="joint model")
        v = params.per_tenor[T]
        ax.plot(grid[grid < 0], v["beta_dn"] * h_down(grid[grid < 0], v["k"]), color="red", lw=1, ls="--",
                label="per-tenor free fit")
        ax.axhline(0, color="k", lw=0.5); ax.axvline(0, color="k", lw=0.5)
        ax.set_title(f"CM-{T}: dVIX vs SPX return, windows {params.horizon} days")
        ax.set_xlabel("SPX return"); ax.set_ylabel("dVIX (points)")
        ax.set_xlim(-0.32, 0.20); ax.legend(fontsize=8, loc="upper right")
    fig.tight_layout()
    fig.savefig(out_png, dpi=110)
    plt.close(fig)


def load_params() -> ResponseParams:
    """run.py's WORKING COPY: output/response_params.json, as the calibration in progress just
    wrote it.  Read during a calibration only.  Pricing uses load_calibration_of_record()."""
    return ResponseParams.from_json(ROOT / "output" / "response_params.json")


def calibration_of_record(runs=None) -> tuple:
    """(folder, manifest) of the calibration in force: output/runs/<run-id>/, named by
    output/runs/LATEST_CALIBRATION.txt.

    Both are committed to git, so every machine that pulls the repo prices with the same,
    reviewed ten numbers.  output/response_params.json is gitignored and machine-local: a
    fresh clone does not have it, and a machine that once ran run.py has its OWN copy."""
    runs = runs or ROOT / "output" / "runs"
    ptr = runs / "LATEST_CALIBRATION.txt"
    if not ptr.exists():
        raise FileNotFoundError(f"no calibration of record: {ptr} is missing -- run `python run.py`")
    run_id, *rest = ptr.read_text().split()
    if (rest[:1] or [""])[0] != "PASS":
        raise RuntimeError(f"the calibration of record {run_id} did not pass its gates -- "
                           f"read its report.txt; a failed calibration must not be priced with")
    folder = runs / run_id
    return folder, json.loads((folder / "manifest.json").read_text())


def load_calibration_of_record(runs=None) -> tuple:
    """(VIX response params, vol-of-vol params, folder, manifest) -- what price.py uses."""
    folder, manifest = calibration_of_record(runs)
    return (ResponseParams.from_json(folder / "response_params.json"),
            ResponseParams.from_json(folder / "vov_params.json"), folder, manifest)


def run(save: bool = True) -> ResponseParams:
    """Fit on the pooled 1..POOL_MAX_HORIZON-day windows (the production calibration)."""
    from .join import load_pooled
    ds = load_pooled()
    params = fit_response(ds)
    if save:
        (ROOT / "output").mkdir(exist_ok=True)
        params.to_json(ROOT / "output" / "response_params.json")
        plot_fit(ds, params, ROOT / "output" / "step4_response_fit.png")
    return params


def lsq_comparison(params: ResponseParams) -> pd.DataFrame:
    """The section-3 exhibit: what least squares (the average) would have said, both branches.

    Row 1 is the calibration as fitted; row 2 is least squares on BOTH branches.  A branch
    that is already least squares in the calibration shows the same numbers in both rows."""
    from .join import load_pooled
    lsq = fit_response(load_pooled(), method="lsq", method_up="lsq")
    rows = []
    used = f"down {params.method_down} / up {params.method_up} (used)"
    for name, p in ((used, params), ("least squares both (NOT used)", lsq)):
        rows.append({"fit": name, "beta_0": p.beta_0, "k": p.k, "lam": p.lam,
                     **{f"dVIX{T}(-10%)": p.dvix(-0.10, T) for T in (30, 120)},
                     **{f"dVIX{T}(-20%)": p.dvix(-0.20, T) for T in (30, 120)},
                     "beta_up_0": p.beta_up_0, "lam_up": p.lam_up,
                     **{f"dVIX{T}(+10%)": p.dvix(0.10, T) for T in (30, 120)},
                     **{f"dVIX{T}(+20%)": p.dvix(0.20, T) for T in (30, 120)}})
    return pd.DataFrame(rows).set_index("fit").round(2)


def horizon_comparison(tenors=config.TENORS) -> pd.DataFrame:
    """Diagnostic: how the fitted parameters move with the window length."""
    from .join import load_pooled
    pool = load_pooled()
    rows = []
    for h in (1, 5, 10, 20):
        p = fit_response(pool[pool["horizon"] == h], horizon=str(h))
        rows.append({"horizon": f"{h}d", "beta_0": p.beta_0, "k": p.k, "lam": p.lam,
                     "beta_up_0": p.beta_up_0, "lam_up": p.lam_up,
                     **{f"dVIX30(-10%)": p.dvix(-0.10, 30), "dVIX30(-20%)": p.dvix(-0.20, 30),
                        "dVIX120(-20%)": p.dvix(-0.20, 120)}})
    p = fit_response(pool)
    rows.append({"horizon": p.horizon, "beta_0": p.beta_0, "k": p.k, "lam": p.lam,
                 "beta_up_0": p.beta_up_0, "lam_up": p.lam_up,
                 "dVIX30(-10%)": p.dvix(-0.10, 30), "dVIX30(-20%)": p.dvix(-0.20, 30),
                 "dVIX120(-20%)": p.dvix(-0.20, 120)})
    return pd.DataFrame(rows).set_index("horizon").round(2)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    params = run()
    print(params.summary())
    pd.set_option("display.width", 250)
    print("\nfit quality per tenor / branch:")
    print(fit_table(params).T.to_string())
    print("\nenvelope points, CM-30:")
    print(pd.DataFrame(params.envelope_points[30]).to_string(index=False))
    print("\nmodel dVIX at the scenario grid:")
    grid = pd.DataFrame({f"T{T}": [params.dvix(s, T) for s in config.SHOCKS] for T in config.TENORS},
                        index=[f"{s:+.0%}" for s in config.SHOCKS]).round(2)
    print(grid.to_string())
