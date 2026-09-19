"""End-to-end smoke tests.

The DATE/date bug that broke the Bloomberg-free path entirely, and the missing
bootstrap_history.py referenced by run.py's own error message, would both have been
caught here.
"""
from __future__ import annotations

import json
import subprocess
import sys

import pandas as pd
import pytest


def run_cli(repo, *args, timeout=300):
    return subprocess.run([sys.executable, *args], cwd=repo, capture_output=True,
                          text=True, timeout=timeout)


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
    r = run_cli(repo, "price.py", "--book", "input/book_TEMPLATE.csv")
    assert "ALL GATES PASS" in r.stdout, r.stdout[-2500:]
    assert r.returncode == 0


def test_price_writes_a_complete_run_folder(repo):
    run_cli(repo, "price.py", "--book", "input/book_TEMPLATE.csv")
    run_id = (repo / "output" / "runs" / "LATEST.txt").read_text().splitlines()[0]
    d = repo / "output" / "runs" / run_id
    for f in ("report.txt", "manifest.json", "positions.csv",
              "pnl_by_scenario.csv", "shocked_curves.csv", "book_input.csv"):
        assert (d / f).exists(), f"{f} missing from {run_id}"


def test_manifest_records_full_provenance(repo):
    run_cli(repo, "price.py", "--book", "input/book_TEMPLATE.csv")
    run_id = (repo / "output" / "runs" / "LATEST.txt").read_text().splitlines()[0]
    m = json.loads((repo / "output" / "runs" / run_id / "manifest.json").read_text())

    assert m["schema_version"] >= 1
    assert m["all_gates_pass"] is True
    # the two things that can make the same book price differently
    assert m["calibration"]["vix_response"]["params"]["beta_0"] > 0
    assert m["market_data"]["latest_curve_date"]
    # and what produced them
    assert m["environment"]["pandas"]
    for g in ("calibration_fresh", "data_fresh", "book_vols"):
        assert g in m["gates"]


def test_book_vols_gate_fails_on_missing_premiums(repo, tmp_path):
    """The far-OTM protection: a book without premiums must FAIL, not price silently."""
    asof = pd.Timestamp((repo / "output" / "runs" / "LATEST.txt").stat().st_mtime, unit="s")
    exp = (pd.Timestamp.today() + pd.Timedelta(days=61)).strftime("%Y-%m-%d")
    p = tmp_path / "no_premiums.csv"
    p.write_text(f"expiry,strike,type,quantity\n{exp},100,C,-50\n")
    r = run_cli(repo, "price.py", "--book", str(p))
    assert "book_vols: FAIL" in r.stdout
    assert r.returncode == 1


def test_bad_book_fails_cleanly_not_with_a_traceback(repo, tmp_path):
    """A malformed book should produce a readable error, not a numpy stack trace."""
    p = tmp_path / "bad.csv"
    p.write_text("expiry,strike,type,quantity,premium\n01/02/2027,20,C,100,1.0\n")
    r = run_cli(repo, "price.py", "--book", str(p))
    assert r.returncode != 0
    assert "YYYY-MM-DD" in (r.stdout + r.stderr)


# ---------------------------------------------------------------- determinism
def test_same_book_prices_identically_twice(repo, price_book):
    b = pd.read_csv(repo / "input" / "book_TEMPLATE.csv")
    a1 = price_book(b).to_csv(index=False)
    a2 = price_book(b).to_csv(index=False)
    assert a1 == a2


# ---------------------------------------------------------------- contracts stay true
def test_documented_files_all_exist(repo):
    for f in ("README.md", "QUESTIONS_FOR_QUANT.md", "config.py",
              "input/INPUT_CONTRACT.md", "input/book_TEMPLATE.csv",
              "data/daily_inputs/README.md", "output/runs/README.md",
              "learning/README.md", "learning/FINDINGS.md",
              "learning/Understanding the Model/README.md"):
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
