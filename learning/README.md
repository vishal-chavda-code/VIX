# learning/

Documentation for whoever inherits this model. Nothing here is imported by the pipeline;
deleting the folder changes no behaviour.

```
Understanding the Model/     six notebooks: how the model works, from first principles
FINDINGS.md                  what is WRONG with it, with numbers
ARCHITECTURE_GAPS.md         how the pipeline should be packaged, and why
PORT_CHECKLIST.md            tick-box runbook for the work machine
```

## Where to start

**New to the model?** → `Understanding the Model/` and read its README first.

**Need to know what is broken?** → `FINDINGS.md`. Four findings: no skew on far-OTM
calls, no starting-level dependence, the pipeline gaps, and the Bloomberg/data issues.

**Running it on the work machine?** → `PORT_CHECKLIST.md`. Phase 0 produces a defensible
finding with no market data at all.

**Changing how it is packaged?** → `ARCHITECTURE_GAPS.md` has the frozen-calibration
design and the argument against refitting on every run.

## Related, outside this folder

| | |
|---|---|
| `../README.md` | the model's own documentation. Section 0b is frozen vs fresh |
| `../QUESTIONS_FOR_QUANT.md` | the seven open decisions that need a reviewer |
| `../input/INPUT_CONTRACT.md` | the book-file spec, for whoever builds the feed |
| `../output/runs/README.md` | what a run folder contains and how to read a manifest |

## Adding more

`Understanding the Model/` is deliberately scoped to *how this model works*. Other
learning material — VIX market background, options theory, desk-specific process — should
go in sibling folders so the two do not tangle.
