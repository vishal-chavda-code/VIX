# Production readiness assessment

Audited 2026-09-19 against the code at that date. Each item was tested, not inferred.

## Verdict

**All five blockers from the first audit are fixed.** Re-audited 2026-09-19 after the
work; every claim below was re-tested.

The remaining items are hardening for a service deployment, not correctness problems.
For a desk tool run by a person, or wired into an automated process with a human
reviewing the verdict block, this is now sound.

| | first audit | now |
|---|---|---|
| test suite | none | **74 tests, 8.4s** |
| book validation | 6 bad inputs silently accepted | **all rejected with row numbers** |
| date parsing | pandas guessed the format | **strict ISO, ambiguous forms rejected** |
| concurrent runs | raced on shared files | **each run writes only its own folder** |
| dependencies | unpinned `>=` | **pinned exactly, recorded per run** |

---

## What IS ready

| | evidence |
|---|---|
| **Deterministic** | same book priced 3x → byte-identical output (md5 match) |
| **Fast enough** | 2,000 positions × 16 scenarios in 0.67s; 100 positions in 0.04s |
| **Fails loudly on the right things** | 8 gates across both entry points; non-zero exit |
| **Full provenance** | every run writes `manifest.json` with the exact parameters, fit window, data date and gate results |
| **No hidden dependencies** | no API keys, no `blpapi`, no network required; verified by grep |
| **Reproducible** | frozen calibration; two runs differ only on parameters or data date, both recorded |
| **Documented** | README, input contract, run-folder contract, six learning notebooks |

---

## FIXED — what the first audit found (kept for the record)

### 1. ~~No test suite~~ — FIXED

There is no `tests/` directory and no test file anywhere. Every change to this model is
verified by running it and eyeballing the report.

**Why it matters here specifically:** this session found a bug (`DATE` vs `date`) that
broke the Bloomberg-free path entirely, and a documentation reference to a script
(`bootstrap_history.py`) that did not exist. Both would have been caught by a smoke test.

**Minimum viable suite:**

```
test_pricing.py       Black-76 vs known values; put-call parity; implied_vol round-trip
test_response.py      dvix() at known inputs; slope -1 at r=0; k=0 reduces to linear
test_book.py          the premium > vol > ATM precedence; every rejection case below
test_smoke.py         price.py end-to-end on book_TEMPLATE.csv, assert gates pass
```

**Done.** `tests/` holds 74 tests running in 8.4 seconds:

```
test_pricing.py      put-call parity to 1e-10, monotonicity, implied-vol round-trip,
                     the far-OTM 4c case, the spot-vs-forward convention claim
test_response.py     slope -1 at r=0 for every k, k=0 reduces to linear, saturation
                     ceiling, tenor ordering, fitted params inside PARAM_RANGES
test_book_input.py   one rejection test per bug this audit found, plus the full
                     premium > vol > ATM precedence and type-whitespace handling
test_smoke.py        both CLIs end to end, run-folder completeness, manifest
                     provenance, determinism, and that every documented file exists
```

Run with `python -m pytest tests/ -q`.

### 2. ~~Book input validation silently accepts bad data~~ — FIXED

Tested with deliberately malformed books. **Four invalid inputs were accepted without
error**, two of which produce wrong numbers rather than obvious garbage:

| input | current behaviour | should be |
|---|---|---|
| `strike = -20` | **accepted**, price = `nan` | reject |
| `strike = 0` | **accepted**, price = 18.56 (nonsense) | reject |
| `type = X` | **accepted, treated as a PUT** | reject |
| `type = " C"` (leading space) | **accepted, treated as a PUT** | strip, then validate |
| `expiry = 01/02/2027` | **accepted, guessed as US month-first** | require ISO, or reject ambiguity |
| `premium = -1.0` | **accepted**, falls back to ATM silently | reject |
| duplicate rows | accepted, double-counted | warn |
| `quantity = "one hundred"` | `TypeError` deep in numpy | reject with a clear message |
| malformed date | `DateParseError` | reject with a clear message |
| expiry in the past | `ValueError`, clear | already correct |
| empty book | empty result, no error | warn |

The `type` cases are the dangerous ones: a CSV export with a trailing space in the type
column **flips every call to a put**, silently, and the run still reports ALL GATES PASS.

**Done.** `vixshock/validate.py` runs at the top of `reprice_book()`. Every case above
now raises `BookError` naming the CSV line numbers, except the three marked *warn*, which
are suspicious rather than wrong. A leading space in `type` is stripped before validation,
so the stray-space bug cannot flip a call to a put.

### 3. ~~Ambiguous dates parse silently wrong~~ — FIXED

Every date column — the book's `expiry`, and both `date` and `contract_expiry` in
`data/daily_inputs/vx_settlements.csv` — is passed to bare `pd.to_datetime()`, which
**guesses** the format.

When both numbers are 12 or under it assumes US month-first order:

```
01/02/2027  ->  2 January 2027      (pandas)
01/02/2027  ->  1 February 2027     (a UK/European feed)
```

That is a **one-month tenor error** with no warning. And the guess can change within a
single column depending on the other rows, so the same value parses two ways in two files.

Documented in `input/INPUT_CONTRACT.md` and `data/daily_inputs/README.md`, but
documentation is not enforcement.

**Done.** `parse_expiry()` uses `format="%Y-%m-%d"` strictly; anything else raises with
the offending values and an explanation of why the ambiguity is rejected rather than
guessed. A parsed expiry that is not a Wednesday also warns, since VIX options always
expire on one.

### 4. ~~Concurrent runs race on shared files~~ — FIXED

`shock.run(save=True)` writes to fixed paths in `output/` — `shocked_curves.csv`,
`book_repricing_detail.csv`, `book_repricing_summary.csv` — in addition to the per-run
folder. Two simultaneous `price.py` runs would interleave writes there.

The per-run folders are safe; only the shared copies collide.

**Done.** `price.py` now calls `shock.run(..., save=False)` and writes only into its own
`output/runs/<run-id>/` folder.

---

## SHOULD FIX before wider use

### 5. ~~Dependencies are unpinned~~ — FIXED

**Done.** Pinned exactly (pandas 2.3.3, numpy 2.4.2, scipy 1.17.1, matplotlib 3.11.2,
requests 2.32.5), and every run records the versions it actually used under
`environment` in its manifest.

### 6. `config.py` is global mutable state

Every module does `import config` and reads module-level constants. There is no way to
price one book under two configurations in the same process, and no record in the output
of what config was in force — except what the manifest happens to capture.

Works fine for a CLI. Would need rework to run as a service.

### 7. No logging discipline

Warnings go to stdout via `logging.warning`. There is no log file, no severity routing,
no run id in the log lines. Diagnosing a failed run means re-running it.

### 8. ~~No schema version on outputs~~ — FIXED

**Done.** `manifest.json` carries `schema_version: 1`.

---

## ACCEPTABLE as-is

- **The per-row Python loop in `reprice_book()`** — O(n) but fast enough (2,000 positions
  in 0.67s). Not worth vectorising.
- **No database** — CSV in, CSV out is right for this scale and keeps the air-gapped
  machine simple.
- **No API** — a CLI is the correct interface for a desk tool run by a person.
- **Matplotlib in the calibration path** — slow, but it only runs yearly.

---

## The honest summary for a reviewer

> *"The model is validated and the pipeline shape is right: calibration is separated from
> pricing, everything fails loudly, and every run records exactly which parameters and
> market data produced it. What it does not have is a test suite, and its book-input
> validation is weak enough that a malformed CSV — a trailing space in the type column,
> for instance — silently flips calls to puts and still reports a clean run.*
>
> *So I would run it today with a person reading the output and sanity-checking the
> inputs. I would not wire it into anything automated until there are tests and input
> validation. Both are days of work, not weeks."*

---

## What is left

Only two items, and neither is a correctness problem:

1. **`config.py` is global mutable state** — fine for a CLI, would need injection to run
   as a service or to price one book under two configurations in one process.
2. **No logging discipline** — warnings go to stdout with no log file, severity routing
   or run id in the line. Diagnosing a failed run means re-running it.

Both are deployment-shape questions. Do them if this becomes a service; ignore them if it
stays a desk tool.

## Keeping it this way

```
python -m pytest tests/ -q
```

74 tests, 8.4 seconds. Run before any commit. The suite asserts *structure* — put-call
parity, slope −1 at zero, the vol precedence, that documented files exist — rather than
specific fitted values, so a legitimate recalibration does not break it while a genuine
regression does.
