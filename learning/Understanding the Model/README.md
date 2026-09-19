# Understanding the Model

Six notebooks that walk through the SPX → VIX shock model from first principles. Written
for someone who knows options and volatility but has never seen this model.

Nothing here is imported by the pipeline — deleting this folder changes no behaviour. It
exists so the next person does not have to reverse-engineer 1,700 lines of code.

Every notebook runs against the **live model** and its real data. If a notebook's numbers
disagree with a fresh `python run.py`, trust the run and re-execute the notebook.

```
python -m jupyter lab           # or open the .ipynb files in VS Code
```

They are already executed, so they read straight through. The value is in changing a
number and re-running.

---

## The six modules

| # | notebook | what it teaches | why you need it |
|---|---|---|---|
| 1 | `01_constant_maturity.ipynb` | **CM = constant maturity.** Why a synthetic fixed-maturity VIX future must be manufactured, the two-contract blend, where spot VIX enters | every other module is built on this series |
| 2 | `02_black76.ipynb` | **Black-76.** Why the forward not spot (the hedging argument), how moneyness drives the architecture, how the book supplies vols | the pricing layer, and the premium-vs-vol decision you make daily |
| 3 | `03_response_function.ipynb` | **The heart.** Three dials, the envelope, pooling, where credible coverage ends | this *is* the risk model — if you can explain this, you can explain the model |
| 4 | `04_stress_validation.ipynb` | **How it proves itself.** 14 crises, the two documented misses, the gate thresholds | the evidence the shock is big enough, and where it is not |
| 5 | `05_vol_of_vol.ipynb` | **The second shock.** Same formula, one sign flipped, so it saturates instead of accelerating | why implied vol is shocked too, and how much it matters |
| 6 | `06_running_it.ipynb` | **Operating it.** Two entry points, the gates, reading a run folder, the air-gapped machine | if you own this day to day, this is the one that matters |

Each ends with self-test questions. Modules 2 and 3 carry answer keys for the arguments a
reviewer is most likely to press on.

**Suggested order:** 1 → 2 → 3 → 4, then 5 and 6 as needed. Module 3 is the load-bearing
one; do it when you are fresh.

---

## The numbers worth memorising

| | |
|---|---|
| **1.57** | VIX points the 30-day future moves per 1% SPX drop |
| **34.4 / 18.8** | VIX points added at 30d / 120d for a −20% SPX shock |
| **55%** | how much the 120-day point moves relative to the front |
| **94.6%** | of historical selloffs beyond 2% produced a VIX move *at or below* the model's prediction — the conservatism, measured |
| **0.94** | R² of the fit on the envelope points, every tenor |
| **1.67** | median model ÷ realised ratio across 14 historical crises |
| **0.72 / 0.81** | the two documented misses: Feb 2018, Aug 2024 |
| **69.87** | highest the 30-day VIX future has ever closed (2020-03-18) |

## The five things to concede before being asked

1. **Overlapping windows.** The 112,910 observations overlap heavily, so the effective
   sample is far smaller and there are no valid standard errors. The fit traces an
   envelope; it does not do inference. Never quote confidence intervals.
2. **Post-2012 reacts harder** than the shipped full-sample calibration (beta_0 227 vs
   192), so the shipped numbers are the *less* conservative choice. One config flag away.
3. **The up branch is an average fit**, not a conservative one, and explains far less
   (R² 0.23–0.37 vs 0.94 down). Asymmetric rigour.
4. **The stress gate allows exactly 2 understated episodes and the actual count is 2.**
   It is a regression guard against new failures, not an independent test.
5. **No starting-level dependence.** The model adds fixed VIX points regardless of where
   VIX starts, so from an elevated base it overshoots. This is the main open question —
   see `../../QUESTIONS_FOR_QUANT.md` Q1.

---

## Where to go next

| | |
|---|---|
| `../FINDINGS.md` | what is wrong with the model, with numbers. Four findings. |
| `../ARCHITECTURE_GAPS.md` | the frozen-calibration design and why refit-on-every-run fails |
| `../PORT_CHECKLIST.md` | the work-machine runbook |
| `../../QUESTIONS_FOR_QUANT.md` | the seven open decisions |
| `../../README.md` | the model's own documentation, section 0b for frozen vs fresh |

## Keeping these honest

The notebooks execute live code, so they rot when the model changes. After any change to
`vixshock/` or `config.py`:

```
cd "learning/Understanding the Model"
python -m jupyter nbconvert --to notebook --execute --inplace 0*.ipynb
```

Then **read the output, not just the exit code.** A notebook can run clean and still
teach something false — when the scenario grid widened from ±20% to ±40%, module 3 kept
executing perfectly while its central claim about the model's reach became wrong.
