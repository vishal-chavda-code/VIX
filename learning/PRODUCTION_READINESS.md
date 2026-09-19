# Production readiness assessment

Audited 2026-09-19 against the code at that date. Each item was tested, not inferred.

## Verdict

**Not production-ready as a hands-off system. Ready as a supervised analyst tool.**

The maths is sound and validated. The pipeline shape is now right — calibration split
from pricing, gates that fail loudly, full run provenance. What is missing is the
engineering around it: there are **no tests**, and **book input validation is weak enough
to silently produce wrong numbers** from a malformed file.

For a risk number a human reads, sanity-checks and signs off on: usable today.
For a number that feeds an automated process unattended: not yet.

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

## BLOCKERS for unattended production

### 1. No test suite — the single biggest gap

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

An afternoon's work. Without it, nobody can safely change this model.

### 2. Book input validation silently accepts bad data

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

**Fix:** a `validate_book()` function at the top of `reprice_book()` that checks types,
signs and ranges, and raises with the offending row numbers. Half a day.

### 3. Ambiguous dates parse silently wrong

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

**Fix:** parse with `format="%Y-%m-%d"` explicitly and raise on anything else, or validate
that every parsed VIX expiry falls on a Wednesday. An hour.

### 4. Concurrent runs race on shared files

`shock.run(save=True)` writes to fixed paths in `output/` — `shocked_curves.csv`,
`book_repricing_detail.csv`, `book_repricing_summary.csv` — in addition to the per-run
folder. Two simultaneous `price.py` runs would interleave writes there.

The per-run folders are safe; only the shared copies collide.

**Fix:** have `price.py` pass `save=False` and write only into its run folder, or drop
the shared copies entirely. An hour.

---

## SHOULD FIX before wider use

### 5. Dependencies are unpinned

`requirements.txt` uses `>=` for everything. A future pandas or scipy release can change
behaviour silently. For a model producing risk numbers, pin exact versions and record
them in the run manifest.

### 6. `config.py` is global mutable state

Every module does `import config` and reads module-level constants. There is no way to
price one book under two configurations in the same process, and no record in the output
of what config was in force — except what the manifest happens to capture.

Works fine for a CLI. Would need rework to run as a service.

### 7. No logging discipline

Warnings go to stdout via `logging.warning`. There is no log file, no severity routing,
no run id in the log lines. Diagnosing a failed run means re-running it.

### 8. No schema version on outputs

`manifest.json` has no `schema_version`. Anything parsing these files downstream will
break silently when a field is renamed.

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

## Priority

1. **Book input validation** — half a day. Prevents silently wrong numbers.
2. **Strict date parsing** — an hour. Same reason, and it affects market data too.
3. **Smoke test + pricing tests** — an afternoon. Makes every future change safe.
4. **Fix the concurrent-write race** — an hour.
5. **Pin dependencies** — minutes.
6. Logging, schema version, config injection — only if this becomes a service.
