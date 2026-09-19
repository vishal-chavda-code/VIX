# INPUT CONTRACT — what to put in this folder

**Audience: whoever (person or AI) builds the file delivery on the source machine.**

This document is the complete specification. It assumes no knowledge of the model.
Produce files matching it and the pricer will consume them with no code changes.

Two kinds of file go in this folder:

1. **A book file** — the positions to price. Required.
2. *(optional)* market-data overrides, which live in `../data/daily_inputs/` and have
   their own README there.

---

## 1. The book file

### Location and name

```
input/book_<YYYY-MM-DD>.csv      e.g. input/book_2026-09-21.csv
```

Any filename works — it is passed explicitly — but dating it is strongly advised so
outputs can be traced back. `.csv` or `.parquet` are both accepted.

### Columns

| column | required | type | meaning |
|---|---|---|---|
| `expiry` | **yes** | date `YYYY-MM-DD` | the option's expiry (VIX options expire Wednesday, 30 days before the third Friday of the following month) |
| `strike` | **yes** | number | strike in VIX points, e.g. `20`, `100` |
| `type` | **yes** | `C` or `P` | call or put. Case-insensitive; only the first letter is read |
| `quantity` | **yes** | integer | **signed**. Positive = long, negative = short |
| `premium` | **strongly preferred** | number | the option's market price per contract, in VIX points (e.g. `0.04` for 4 cents). **Use the mid of the bid/ask where available** |
| `vol` | optional | decimal | implied vol as a decimal, `1.27` = 127%. Only used when `premium` is absent |
| `forward` | optional | number | the VIX **future** for this expiry. Omit and the model interpolates from its own curve |

Extra columns are ignored, so carrying an internal position id or book name through is fine.

### Worked example

```csv
expiry,strike,type,quantity,premium,vol,forward
2026-10-21,20,C,100,1.25,,
2026-10-21,30,C,-200,0.13,,
2026-12-16,100,C,-50,0.04,,
2026-11-18,16,P,-100,0.98,,
```

See `book_TEMPLATE.csv` in this folder.

---

## 2. The rules that matter

### 2.0 What to put in `vol` if you do not have it (or do not want to use it)

**Leave it empty.** That is the recommended case, not a degraded one.

You may also omit the `vol` column entirely. Both are treated identically.

```csv
expiry,strike,type,quantity,premium,vol,forward
2026-12-16,100,C,-50,0.04,,                     <- vol blank: CORRECT
```
```csv
expiry,strike,type,quantity,premium
2026-12-16,100,C,-50,0.04                       <- no vol column at all: ALSO CORRECT
```

The model backs the implied vol out of the premium itself, using its own forward, day-count,
rate and Black-76. The resulting mark is **exact** — it reproduces the premium you supplied to
the cent.

Accepted as empty: a blank field, a missing column, `NaN`, or an empty string. **Do not** put
`0`, `-1`, `N/A`, `NULL` or any other sentinel in `vol` — a zero is a *value*, and the model
would try to price at zero volatility.

Only supply `vol` when you have **no premium** for that row. Never supply it as a
belt-and-braces duplicate: it is not needed, and if it disagrees with the premium you will get
a warning on every run for no benefit.

| what you have | `premium` | `vol` |
|---|---|---|
| a market price (the normal case) | the price | **leave blank** |
| no price, but a trusted vol | blank | the vol as a decimal |
| a price *and* a vol you want cross-checked | the price | the vol — premium wins, gap is logged |
| neither | — | **the run FAILS.** Get one of them |

### 2.1 Supply `premium`, not `vol`

The pricer prefers `premium` over `vol`, and this is deliberate.

A premium is an **observable**. A vol is **derived** — whoever produced it had to choose a
forward, a day-count, a rate and a model. If their choices differ from this model's, the vol
is inconsistent with the pricer that consumes it.

Measured, on a 90-day 100-strike call with a 4-cent premium:

| how the vol was derived | implied vol | error |
|---|---|---|
| this model (forward = the VIX future, 91/365) | 126.92% | — |
| **struck against spot VIX instead of the future** | **133.80%** | **+6.87 vol points** |
| 252 business days | 126.75% | −0.17 |
| rate = 0 | 126.78% | −0.14 |

Feeding that spot-derived vol in produces a price of **0.0636** against a market price of
**0.0400** — a **59% mark error** from a vol that is "correct" under its own convention.

Day-count and rate barely matter. **The forward is what matters.** Many systems quote VIX
option vols against spot VIX, which is the wrong underlying for this product.

Supplying the premium lets the model invert it with its own conventions, so the mark is exact
by construction.

### 2.2 Mid vs liquidation price

**Use mid where you have it.** The model's output is a *change* in value, and mid carries no
assumption about which way you would trade.

If you only have the liquidation side, use it and record that you did — the effect is small.
A 0.03/0.05 quote spans ~7 vol points but only ±5% of the scenario P&L. For a short position
the liquidation side is the ask, which gives a slightly larger loss: conservative, the right
direction to err.

### 2.3 Populate `premium` for EVERY position

A position with neither `premium` nor `vol` falls back to an at-the-money vol curve. For
far-out-of-the-money calls that marks them at **effectively zero** — a 100-strike call with
the future at 19 prices at 0.000009 against a market price of 0.04.

**The run FAILS if any position falls back** (the `book_vols` gate). This is intentional. A
partial file produces a loud failure, not a quiet understatement.

If both `premium` and `vol` are given, the premium wins and a gap of more than 1 vol point
between them is logged — useful as a check on the vol source.

### 2.4 Signs and units

- `quantity` is **signed**: `-200` means short 200 contracts
- `strike` and `premium` are in **VIX points**, not dollars. No contract multiplier is applied
- `vol` is a **decimal**: `1.27`, not `127`
- P&L in the output is therefore in VIX points × quantity — apply the multiplier downstream

### 2.5 Validation the pricer performs

It will reject or flag:

| condition | behaviour |
|---|---|
| an option expiring on or before the evaluation date | **error**, run stops |
| `premium` below intrinsic value | warning, falls back to `vol` or the ATM curve |
| any position without premium or vol | `book_vols` gate **FAILS** |
| `premium` and `vol` disagreeing by more than 1 vol point | warning, premium is used |
| the curve data being older than 7 days | `data_fresh` gate **FAILS** |

---

## 3. Market data (only if the machine has no internet)

If the pricing machine cannot reach CBOE, put today's rows in `../data/daily_inputs/` —
that folder has its own README with the exact formats. Three files:

- `vx_settlements.csv` — one row per listed VIX future per day. **All** listed monthly
  contracts, not just the front: the 120-day curve point needs the 4th–5th month
- `vix_spot.csv` — spot VIX close
- `spx.csv` — SPX close (**calibration only**, not needed for daily pricing)

**SPX is not a pricing input.** The SPX move is a scenario the model applies, not something
it observes. SPX is only read when recalibrating, which happens yearly.

---

## 4. Running it

```
python price.py --book input/book_2026-09-21.csv
```

Takes about a second. Output lands in `output/runs/<run-id>/` — see the README there.

Exit code 0 means every gate passed. Non-zero means do not use the numbers; the verdict
block at the end of the run says which gate failed and why.

---

## 5. Quick checklist before delivering a file

- [ ] one row per position, signed `quantity`
- [ ] `premium` populated for **every** row (mid where available)
- [ ] `expiry` is `YYYY-MM-DD` and in the future
- [ ] `strike` in VIX points, `type` is C or P
- [ ] `vol` left blank unless there is no premium for that row
- [ ] filename carries the date
