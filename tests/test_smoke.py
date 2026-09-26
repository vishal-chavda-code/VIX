"""End-to-end smoke tests.

The DATE/date bug that broke the Bloomberg-free path entirely, and the missing
bootstrap_history.py referenced by run.py's own error message, would both have been
caught here.

Every price.py run here uses a book dated DATA_DATE, the date the shipped market data
ends on.  Before 2026-09-26 these tests priced "as of today" and started failing on their
own a week after the data was last refreshed -- the result depended on the wall clock.
"""
from __future__ import annotations

import json
import subprocess
import sys

import pandas as pd
import pytest

from conftest import DATA_DATE, TEMPLATE

HEADER = ("cusip,issuer_name,expiry,strike,type,quantity,multiplier,premium,premium_source,"
          "src_bid,src_ask,src_close,forward")


def run_cli(repo, *args, timeout=300):
    return subprocess.run([sys.executable, *args], cwd=repo, capture_output=True,
                          text=True, timeout=timeout)


def price_cli(repo, book, *more):
    """price.py without the network download: tests must not depend on CBOE or rewrite data/raw."""
    return run_cli(repo, "price.py", "--book", str(book), "--no-refresh", *more)


def latest_manifest(repo) -> dict:
    run_id = (repo / "output" / "runs" / "LATEST.txt").read_text().splitlines()[0]
    return json.loads((repo / "output" / "runs" / run_id / "manifest.json").read_text())


# ---------------------------------------------------------------- the CLIs exist and parse
@pytest.mark.parametrize("script", ["price.py", "run.py", "bootstrap_history.py"])
def test_entry_point_exists_and_has_help(repo, script):
    assert (repo / script).exists(), f"{script} is referenced in the docs but does not exist"
    r = run_cli(repo, script, "--help", timeout=60)
    assert r.returncode == 0, r.stderr


def test_every_module_imports(repo):
    """Catches a syntax error or a broken import anywhere in the package."""
    mods = [p.stem for p in (repo / "vixshock").glob("*.py") if p.stem != "__init__"]
    r = run_cli(repo, "-c", "import " + ", ".join(f"vixshock.{m}" for m in mods), timeout=120)
    assert r.returncode == 0, r.stderr


# ---------------------------------------------------------------- price.py end to end
def test_price_template_book_passes_all_gates(repo):
    r = price_cli(repo, TEMPLATE)
    assert "ALL GATES PASS" in r.stdout, r.stdout[-2500:] + r.stderr[-1500:]
    assert r.returncode == 0


def test_price_writes_a_complete_run_folder(repo):
    price_cli(repo, TEMPLATE)
    run_id = (repo / "output" / "runs" / "LATEST.txt").read_text().splitlines()[0]
    d = repo / "output" / "runs" / run_id
    for f in ("report.txt", "manifest.json", "positions.csv", "pnl_by_scenario.csv",
              "floor_report.csv", "floors.csv", "shocked_curves.csv", "book_input.csv"):
        assert (d / f).exists(), f"{f} missing from {run_id}"


def test_manifest_records_full_provenance(repo):
    price_cli(repo, TEMPLATE)
    m = latest_manifest(repo)
    assert m["schema_version"] >= 2
    assert m["all_gates_pass"] is True
    assert m["asof"] == DATA_DATE and m["asof_source"] == "book filename"
    # the two things that can make the same book price differently
    assert m["calibration"]["vix_response"]["params"]["beta_0"] > 0
    assert m["calibration"]["vix_response"]["method_up"] in ("lsq", "envelope")
    assert m["market_data"]["dates"]["vix_futures_cm"] == DATA_DATE
    # and what produced them
    assert m["environment"]["pandas"]
    for g in ("calibration_fresh", "curve_date", "book_vols", "book_forwards"):
        assert g in m["gates"]


def test_positions_output_is_keyed_by_cusip(repo):
    price_cli(repo, TEMPLATE)
    run_id = (repo / "output" / "runs" / "LATEST.txt").read_text().splitlines()[0]
    pos = pd.read_csv(repo / "output" / "runs" / run_id / "positions.csv", dtype={"cusip": str})
    book = pd.read_csv(repo / TEMPLATE, dtype={"cusip": str})
    assert pos.columns[0] == "cusip"
    assert set(pos["cusip"]) == set(book["cusip"])


def test_book_vols_gate_fails_on_missing_premiums(repo, tmp_path):
    """The far-OTM protection: a book without premiums must FAIL, not price silently."""
    p = tmp_path / f"no_premiums_{DATA_DATE}.csv"
    p.write_text(f"{HEADER}\nX1,VOLATILITY INDEX (VIX),2026-11-18,100,C,-50,100,,,,,,18.7049\n")
    r = price_cli(repo, p)
    assert "book_vols: FAIL" in r.stdout, r.stdout[-2000:] + r.stderr[-1000:]
    assert r.returncode == 1


def test_blank_vix_forward_on_a_monthly_expiry_uses_that_futures_settle(repo, tmp_path):
    p = tmp_path / f"monthly_{DATA_DATE}.csv"
    p.write_text(f"{HEADER}\nX1,VOLATILITY INDEX (VIX),2026-10-21,20,C,100,100,1.25,mid,1.20,1.30,1.25,\n")
    r = price_cli(repo, p)
    assert "book_forwards: PASS" in r.stdout, r.stdout[-2000:] + r.stderr[-1000:]
    assert "VX settle" in r.stdout


def test_book_forwards_gate_fails_on_a_weekly_with_no_forward(repo, tmp_path):
    """A weekly has no listed future in the data, so a blank forward falls back to the
    interpolated CM curve.  The base price still matches the mark -- the vol absorbs the
    error -- so this must FAIL rather than pass."""
    p = tmp_path / f"weekly_{DATA_DATE}.csv"
    p.write_text(f"{HEADER}\nX1,VOLATILITY INDEX (VIX),2026-10-28,20,C,100,100,1.25,mid,1.20,1.30,1.25,\n")
    r = price_cli(repo, p)
    assert "book_forwards: FAIL" in r.stdout, r.stdout[-2000:] + r.stderr[-1000:]
    assert r.returncode == 1


def test_curve_date_gate_fails_when_no_data_for_book_date(repo, tmp_path):
    """Monday's book against Friday's curve must FAIL, and price nothing."""
    p = tmp_path / "book_2026-09-21.csv"          # a Monday after the last data on disk
    p.write_text((repo / TEMPLATE).read_text())
    r = price_cli(repo, p)
    assert "curve_date: FAIL" in r.stdout, r.stdout[-2000:]
    assert "BOOK P&L" not in r.stdout
    assert r.returncode == 1


@pytest.mark.parametrize("name", ["book.csv", "book_20260918.csv", "book_2026-09-18_vs_2026-09-17.csv"])
def test_book_filename_must_carry_exactly_one_iso_date(repo, tmp_path, name):
    p = tmp_path / name
    p.write_text((repo / TEMPLATE).read_text())
    r = price_cli(repo, p)
    assert r.returncode != 0
    assert "YYYY-MM-DD" in (r.stdout + r.stderr)


def test_bad_book_fails_cleanly_not_with_a_traceback(repo, tmp_path):
    """A malformed book should produce a readable error, not a numpy stack trace."""
    p = tmp_path / f"bad_{DATA_DATE}.csv"
    p.write_text(f"{HEADER}\nX1,VOLATILITY INDEX (VIX),01/02/2027,20,C,100,100,1.0,mid,,,,18.0\n")
    r = price_cli(repo, p)
    assert r.returncode != 0
    assert "YYYY-MM-DD" in (r.stdout + r.stderr)
    assert "Traceback" not in r.stderr


# ---------------------------------------------------------------- determinism
def test_same_book_prices_identically_twice(repo, price_book):
    from vixshock.validate import read_book
    b = read_book(repo / TEMPLATE)
    a1 = price_book(b).to_csv(index=False)
    a2 = price_book(b).to_csv(index=False)
    assert a1 == a2


# ---------------------------------------------------------------- contracts stay true
def test_documented_files_all_exist(repo):
    for f in ("README.md", "QUESTIONS_FOR_QUANT.md", "config.py",
              "input/INPUT_CONTRACT.md", TEMPLATE,
              "data/daily_inputs/README.md", "output/runs/README.md"):
        assert (repo / f).exists(), f"{f} is referenced by the docs but missing"


def test_no_live_bloomberg_dependency(repo):
    """The model must stay Bloomberg-free: a frozen file is fine, an import is not."""
    needle = "blp" + "api"          # split so this test does not match itself
    hits = [p for p in repo.rglob("*.py")
            if ".venv" not in str(p) and "tests" not in p.parts
            and needle in p.read_text(encoding="utf-8", errors="ignore")]
    assert not hits, f"blpapi referenced in: {hits}"


def test_scenario_grid_spans_both_directions(repo):
    import config
    assert min(config.SHOCKS) < -0.15 and max(config.SHOCKS) > 0.15
    assert 0.0 not in config.SHOCKS


def test_the_feeds_exact_layout_prices_end_to_end(repo, tmp_path):
    """The desk's 11 columns exactly, VIX and SPX, one option settling today: ALL GATES PASS."""
    p = tmp_path / f"book_{DATA_DATE}.csv"
    p.write_text(
        "expiry,strike,type,quantity,vol,premium,src_bid,src_ask,src_close,cusip,issuer_name\n"
        "2026-10-21,20,C,100,,1.25,1.20,1.30,1.25,FEEDVIX01,VOLATILITY INDEX (VIX)\n"
        "2026-12-16,100,C,-50,,0.04,0.00,0.08,0.04,FEEDVIX02,VOLATILITY INDEX (VIX)\n"
        "2026-12-18,6900,P,20,,30.70,30.20,31.20,30.70,FEEDSPX01,S&P 500 INDEX\n"
        f"{DATA_DATE},7600,P,5,,1.00,0.95,1.05,1.00,FEEDSPX0DTE,S&P 500 INDEX\n")
    r = price_cli(repo, p)
    assert "ALL GATES PASS" in r.stdout, r.stdout[-2500:] + r.stderr[-1500:]
    assert "FEEDSPX0DTE" in r.stdout and "excluded" in r.stdout
    m = latest_manifest(repo)
    assert m["n_positions_by_underlying"] == {"VIX": 2, "SPX": 1}
    assert m["excluded_expiring_on_pricing_date"] == ["FEEDSPX0DTE"]
