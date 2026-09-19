# learning/

Documentation for whoever inherits this model. Nothing here is imported by the pipeline;
deleting the folder changes no behaviour.

```
Understanding the Model/        six notebooks: how the model works, from first principles
FINDINGS.md                     what is WRONG with it, with numbers
PRODUCTION_READINESS.md         audited engineering state: what is solid, what is not
ARCHITECTURE_GAPS.md            how the pipeline should be packaged, and why
PORT_CHECKLIST.md               tick-box runbook for the work machine
SPX_EXTENSION_PLAN.md           adding SPX options: scope, blockers, sequencing
VOL_SURFACE_DATA_REQUEST.md     the data ask, written to hand to the surface team
```

## Where to start

| you are... | read |
|---|---|
| **new to the model** | `Understanding the Model/` — its README first |
| **preparing to defend it** | `FINDINGS.md`, then notebooks 3 and 4 |
| **asked "is it production ready?"** | `PRODUCTION_READINESS.md` |
| **running it on the work machine** | `PORT_CHECKLIST.md` — Phase 0 needs no market data |
| **changing how it is packaged** | `ARCHITECTURE_GAPS.md` |
| **scoping the SPX extension** | `SPX_EXTENSION_PLAN.md`, then `VOL_SURFACE_DATA_REQUEST.md` |

## The state of things, 2026-09-19

**The model itself:** validated. Median model/realised ratio 1.67 across 14 historical
crises, 94.6% coverage of all selloffs beyond 2%, R² 0.94 on the envelope points. Two
documented misses, both short-vol unwinds (Feb 2018 at 0.72, Aug 2024 at 0.81).

**The engineering:** all five production blockers from the first audit are closed — 74
tests, strict input validation, ISO-only dates, no write race, pinned dependencies. What
remains is deployment shape (logging, config injection), not correctness.

**Open findings, in `FINDINGS.md`:**

1. **No skew** — far-OTM calls marked at effectively zero. *Largely mitigated*: the book
   now takes a premium per position and the `book_vols` gate fails the run without one.
2. **No starting-level dependence** — the model adds fixed VIX points regardless of where
   VIX starts. **The main open question.** Q1 for the quant.
3. ~~Pipeline gaps~~ — fixed: calibration split from pricing, freshness gates, run manifests.
4. ~~Bloomberg dependency~~ — fixed: code removed, frozen historical file, VVIX fallback.

**Also open:**

- **Flat extrapolation past the last tenor.** A 400-day option prices off the 150-day
  forward and the run exits 0. Should warn or gate. (Tenors were extended 120 → 150 on
  2026-09-19; 180 was tried and **rejected by the stress gate**.)
- **No contract multiplier.** P&L is index points × contracts, not currency. Harmless for
  one product, blocking for two — see `SPX_EXTENSION_PLAN.md`.

## Related, outside this folder

| | |
|---|---|
| `../README.md` | the model's own documentation. Section 0b is frozen vs fresh |
| `../QUESTIONS_FOR_QUANT.md` | the open decisions that need a reviewer |
| `../input/INPUT_CONTRACT.md` | the book-file spec, for whoever builds the feed |
| `../data/daily_inputs/README.md` | market-data file formats, if there is no network |
| `../output/runs/README.md` | what a run folder contains and how to read a manifest |
| `../tests/` | 74 tests, `python -m pytest tests/ -q` |

## Adding more

`Understanding the Model/` is deliberately scoped to *how this model works*. Other
material — VIX market background, options theory, desk process — belongs in sibling
folders so the two do not tangle.
