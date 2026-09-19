# learning/ — how this model works, and what is wrong with it

Written for whoever inherits this model. Nothing in here is imported by the
pipeline; deleting the folder changes no behaviour. It exists so the next person
does not have to reverse-engineer 1,700 lines of code from scratch.

Every notebook runs against the **live model** and its real data files. If a
notebook's numbers disagree with a fresh `python run.py`, trust the run — and
re-execute the notebook, because something has changed.

---

## Start here

| | read when |
|---|---|
| **`FINDINGS.md`** | you need to know what is wrong with the model and how big it is. Four findings with numbers. |
| **`ARCHITECTURE_GAPS.md`** | you are changing how the pipeline is packaged, or defending the frozen-calibration design. |
| **`PORT_CHECKLIST.md`** | you are running this on the work machine. A tick-box runbook. |

## The notebooks, in order

Run them with Jupyter (`python -m jupyter lab`) or in VS Code. They are already
executed, so they can be read straight through — but the value is in changing a
number and re-running.

| # | notebook | what it teaches |
|---|---|---|
| 1 | `01_constant_maturity.ipynb` | **CM = constant maturity.** Why a synthetic fixed-maturity VIX future has to be manufactured at all, how the two-contract blend works, and where spot VIX enters. |
| 2 | `02_black76.ipynb` | **Black-76.** Why the forward and not spot VIX (the hedging argument), how moneyness drives the whole architecture, and how the book supplies vols — premium vs vol, and the `vol=0` trap. |
| 3 | `03_response_function.ipynb` | **The heart of the model.** Three dials, the envelope, pooling, and where the model's credible coverage ends. |
| 4 | `04_stress_validation.ipynb` | **How the model proves itself.** Fourteen historical crises, the two documented misses, and why the gate thresholds are what they are. |

Each notebook ends with self-test questions. Modules 2 and 3 also carry an answer
key for the arguments a reviewer is most likely to press on.

### Not yet written

Vol-of-vol (`volofvol.py`) and the operational side of reading a run report have
no notebook. The README covers both adequately for now.

---

## The five numbers worth memorising

| | |
|---|---|
| **1.57** | VIX points the 30-day future moves per 1% SPX drop |
| **34.4 / 18.8** | VIX points added at 30d / 120d for a −20% SPX shock |
| **94.6%** | of all historical selloffs beyond 2% produced a VIX move *at or below* the model's prediction — the conservatism, measured |
| **0.94** | R² of the fit on the envelope points, at every tenor |
| **1.67** | median model ÷ realised ratio across 14 historical crises |

## The three things to concede before being asked

1. **Overlapping windows.** The 112,910 observations are heavily overlapping, so the
   effective sample is far smaller and there are no valid standard errors. The fit
   traces an envelope; it does not do inference. Never quote confidence intervals.
2. **Post-2012 reacts harder** than the shipped full-sample calibration (beta_0 227 vs
   192), so the shipped numbers are the *less* conservative choice. One config flag away.
3. **The up branch is an average fit**, not a conservative one, and explains far less
   (R² 0.23–0.37 vs 0.94 on the down branch). Asymmetric rigour.

---

## Keeping this folder honest

The notebooks execute live code, so they rot when the model changes. Re-run them
after any change to `vixshock/` or `config.py`:

```
cd learning
python -m jupyter nbconvert --to notebook --execute --inplace 0*.ipynb
```

Then read the output, not just the exit code. A notebook can run clean and still
teach something false — when the scenario grid was widened from ±20% to ±40%,
module 3 kept executing perfectly while its central claim about the model's reach
became wrong.
