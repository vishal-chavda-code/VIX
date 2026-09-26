# INPUT CONTRACT — what to put in this folder

**Audience: whoever (person or AI) builds the file delivery on the source machine.**

This document is the complete specification. It assumes no knowledge of the model.
Produce files matching it and the pricer will consume them with no code changes.

Two kinds of file go in this folder:

1. **A book file** — the positions to price. Required.
2. *(optional)* market-data files, in `../data/daily_inputs/` — only needed if the pricing
   machine cannot download the day's public settles itself (section 3).

**The book carries positions and premiums only.** Market prices — VIX futures, spot VIX, the
SPX close — are downloaded by the pricer each run, not supplied in the book.

The book may hold **VIX options and SPX options** together. P&L is reported per product
and for the whole book, in currency.

---

## 1. The book file

### Location and name — the name carries the pricing date

```
input/book_<YYYY-MM-DD>.csv      e.g. input/book_2026-09-18.csv
```

**The date in the filename is the pricing date.** Tenors, the VIX futures curve and the
SPX close are all read as of that date — never as of "today". The filename must contain
**exactly one** date, written `YYYY-MM-DD`. No date, two dates, or `20260918` are rejected.

The date must be the **date the marks were taken**. The run fails (gate `curve_date`)
unless the market data has a row for exactly that date: see section 2.6 for why.

`.csv` or `.parquet` are both accepted.

### Columns

| column | required | type | meaning |
|---|---|---|---|
| `cusip` | **yes** | text | the position key. **Unique** per row, never blank. Every output row carries it |
| `issuer_name` | **yes** | text | the product. Exactly one of the names in the table below |
| `expiry` | **yes** | date `YYYY-MM-DD` | the option's expiry |
| `strike` | **yes** | number | in index points: VIX points for VIX options, SPX points for SPX options |
| `type` | **yes** | `C` or `P` | call or put. Case-insensitive; only the first letter is read |
| `quantity` | **yes** | integer | **signed**, in contracts. Positive = long, negative = short |
| `multiplier` | optional | number | contract multiplier. Taken from the product table below; if sent, it must match |
| `premium` | **yes** (column) | number | the option's market price per contract, in index points. Blank only if you truly have none — the run then fails (`book_vols`) |
| `premium_source` | optional | `premium` `bid` `ask` `mid` `close` | which field the premium came from. Absent or blank = `premium`: the feed's own premium field, as delivered |
| `forward` | optional | number | **leave it out.** VIX: the model uses that day's settle of the VIX future expiring with the option. SPX: SPX close × carry. A value here overrides either (see 2.4) |
| `src_bid` | **yes** (column) | number | the bid the premium was taken from. May be blank per row |
| `src_ask` | **yes** (column) | number | the ask. May be blank per row |
| `src_close` | **yes** (column) | number | the close. May be blank per row |

Extra columns are ignored, so carrying an internal id or book name through is fine. Column
**names** are matched ignoring case and surrounding spaces (`expiry, strike` and `Expiry`
both work); a leading Excel byte-order mark is fine.

**The desk feed's layout is accepted as it stands**, with nothing added:

```
expiry,strike,type,quantity,vol,premium,src_bid,src_ask,src_close,cusip,issuer_name
```

**No longer part of the spec:** `lots` (always 1 — if present and not 1 the run fails,
because it would mean `quantity` is not the whole position) and `vol` (see 2.2).

### Products

| `issuer_name` (case and spacing ignored) | product | `multiplier` |
|---|---|---|
| `VOLATILITY INDEX (VIX)` | VIX options | 100 |
| `S&P 500 INDEX` | SPX options | 100 |

Any other `issuer_name` **fails the run**. There is no default. A new product (XSP,
mini-VIX, weeklies under a different name) must be added to `config.PRODUCTS` with its
multiplier first. The multiplier comes from that table, and a `multiplier` in the book, if
sent, must agree.

Note what the multiplier can *not* catch: XSP (the mini-SPX, a tenth of the index) also
has a multiplier of 100. If an XSP option ever arrived labelled `S&P 500 INDEX`, the tell
is its strike — a tenth of SPX's — so SPX strikes outside 20%–500% of the SPX forward are
**rejected** as the wrong product or wrong units.

### Worked example

```csv
cusip,issuer_name,expiry,strike,type,quantity,multiplier,premium,src_bid,src_ask,src_close
TPLVIX001,VOLATILITY INDEX (VIX),2026-10-21,20,C,100,100,1.25,1.20,1.30,1.25
TPLVIX003,VOLATILITY INDEX (VIX),2026-12-16,100,C,-50,100,0.04,0.00,0.08,0.04
TPLSPX002,S&P 500 INDEX,2026-12-18,6900,P,20,100,30.70,30.20,31.20,30.70
```

See `book_TEMPLATE_2026-09-18.csv` in this folder (a template, not real positions).

---

## 2. The rules that matter

### 2.1 DATE FORMAT — `YYYY-MM-DD`, and only that

**This is the easiest way to get a silently wrong answer, so it comes first.** It applies
to the `expiry` column *and* to the date in the filename.

Anything else is **rejected**, with the row numbers — except a midnight time suffix
(`2026-11-18 00:00:00`), which many exports add and which is dropped. `01/02/2027` is 2 January under a
US reading and 1 February under a European one; guessing wrong shifts the tenor by a
month with no warning. The loader does not guess.

- If your tool is Excel, format the column as text first — Excel re-renders dates in the
  machine's locale on save.
- **Verify after writing the file.** Open the CSV in a text editor and look at the raw
  characters.
- A VIX option expires on a **Wednesday** (30 days before the third Friday of the
  following month); a VIX expiry on any other day produces a warning. SPX options expire
  on Fridays (monthlies) or any weekday (weeklies), so no such check applies to them.

### 2.2 Supply `premium`, and nothing in its place

The model backs the implied vol out of the premium itself, using its own forward,
day-count, rate and Black-76, so the base price reproduces the premium exactly.

A premium is an **observable**. An imported vol is **derived** — whoever produced it chose
a forward, a day-count, a rate and a model. Measured on a 90-day 100-strike VIX call with a
4-cent premium: a vol struck against spot VIX instead of the future reads 133.80% against
the correct 126.92%, and feeding it back produces a price of 0.0636 against a market of
0.0400 — a **59% mark error** from a vol that is "correct" under its own convention.

`vol` is therefore no longer in the spec. If a legacy file still carries a `vol` column it is
used only where `premium` is blank, and never for a `0`, `-1` or other sentinel (rejected).

### 2.3 `premium_source`, `zero_bid`: record where the mark came from

By default the model records `premium_source` = `premium`: the feed's own premium field,
taken as delivered, with no claim about which side of the market it is. That is the normal
case and needs no column at all.

If you know which field it is, say so: `mid`, `bid`, `ask` or `close`. Those are claims, and
where `src_bid` / `src_ask` / `src_close` are populated the run checks the premium matches
the one claimed (half a cent tolerance) and warns if not. Anything else is rejected.

**Use mid where you have it.** The output is a *change* in value, and mid carries no view
on which way you would trade. A 0.03/0.05 quote spans ~7 vol points but only ±5% of the
scenario P&L.

The model flags every position whose **`src_bid` is 0** (`zero_bid` in the output). These
are the least reliable marks — a far-out-of-the-money option nobody is bidding. They are
flagged and listed, not changed.

### 2.4 `forward`: leave it out

**VIX rows.** A VIX option settles to the VIX **future** expiring the same day, so that
future's settle on the pricing date *is* the option's forward. The pricer downloads every
listed future's settle each run and looks it up by expiry date — the Dec option gets the Dec
future. A lookup, not an estimate.

The one case with no listed future is a **weekly** VIX option (a Wednesday that is not a
monthly expiry): the pricer only carries the monthly futures. With no `forward` in the book
it would have to interpolate its constant-maturity curve between other contracts, so it
**fails** the run (gate `book_forwards`) instead — that error hides: the implied vol is
inverted from the same interpolated forward, so the base price still matches the mark and
only the shocked price is wrong. Measured on 2026-09-18: the Oct future settled 18.04; the
interpolated curve says 17.84. For a weekly, put its VIX future in `forward`.

**SPX rows.** The pricer uses the SPX close on the pricing date and a flat documented carry
(`config.SPX_DIVIDEND_YIELD`). No SPX futures are needed.

A value in `forward` always overrides, for either product. For a VIX row it is compared with
the listed settle, and a difference over 0.01 is logged.

### 2.5 Signs and units

- `quantity` is **signed** and in **contracts**: `-200` means short 200 contracts
- `strike`, `premium`, `forward`, `src_*` are in **index points**, not currency
- the output P&L **is** in currency: price change × `quantity` × `multiplier`

### 2.6 What the pricer rejects, fails, or flags

| condition | behaviour |
|---|---|
| filename without exactly one `YYYY-MM-DD` date | **rejected**, nothing priced |
| market data (VIX curve, spot VIX, SPX close if SPX is held) not from the filename date | gate `curve_date` **FAILS**, nothing priced |
| a missing required column, a non-ISO expiry | **rejected** |
| an option that expired **before** the pricing date | **rejected** — a stale file |
| an option expiring **on** the pricing date | **excluded** and listed in the report — it has settled (normal for SPX, which has daily expiries) |
| an SPX strike outside 20%–500% of the SPX forward | **rejected** — wrong product (XSP?) or wrong units |
| `vol` = 0 or negative | ignored where the row has a premium; **rejected** where it would be used |
| unknown `issuer_name`, `multiplier` not matching the product table | **rejected** |
| blank or duplicated `cusip` | **rejected** — aggregate each contract into one row |
| `lots` present and not 1 | **rejected** |
| `premium_source` other than `premium`, `bid`, `ask`, `mid`, `close` (or blank) | **rejected** |
| an SPX position with no usable premium | priced at `VOL_FLOOR_SPX`, flagged; gate `book_vols` **FAILS** |
| a VIX position with no premium | falls back to an ATM vol curve; gate `book_vols` **FAILS** |
| a VIX position whose expiry has no listed future (a weekly) and no `forward` | falls back to the interpolated curve; gate `book_forwards` **FAILS** |
| no vol reproduces the `premium` (below intrinsic against the forward, or above what 500% vol gives) | warning; falls back as above, gate `book_vols` **FAILS** |
| `premium` not matching the `src_*` column it claims | warning |
| `src_bid` = 0 | flagged `zero_bid` |
| a VIX expiry that is not a Wednesday | warning |
| the same contract under two cusips | warning — a duplicated file would double-count |

**Why the curve date must match exactly:** Monday's marks priced off Friday's forwards do
not fail on their own. The implied vol is inverted from the premium with the stale forward,
so the base price still reproduces the mark to the cent. The whole error lands in the
shocked number, which is the one that gets reported.

---

## 3. Market data — downloaded by the pricer

Each `price.py` run first downloads the latest public data itself — no key, no account:
every listed VIX future's settle, spot VIX and the SPX close, all from CBOE. Settles for
a trading day are there that evening, so a book marked at Friday's close can be priced from
Friday evening on.

Only if the pricing machine cannot reach those sites, put the pricing date's rows in
`../data/daily_inputs/` — that folder has its own README with the exact formats:

- `vx_settlements.csv` — one row per listed VIX future per day. **All** listed monthly
  contracts, not just the front
- `vix_spot.csv` — spot VIX close
- `spx.csv` — SPX close. **Needed for pricing whenever the book holds SPX options** (it is
  the base of every SPX forward), and for recalibration

The SPX *move* is still a scenario the model applies, not something it observes. What it
observes is the SPX **level** on the pricing date, because an SPX option is priced off it.

---

## 4. Running it

```
python price.py --book input/book_2026-09-18.csv
```

Takes about a second. Output lands in `output/runs/<run-id>/` — see the README there.

Exit code 0 means every gate passed. Non-zero means do not use the numbers; the verdict
block at the end of the run says which gate failed and why.

---

## 5. Quick checklist before delivering a file

- [ ] filename carries the marking date as `YYYY-MM-DD`, and only that one date
- [ ] one row per contract, unique `cusip`, signed `quantity` in contracts
- [ ] `issuer_name` exactly as in the product table (`multiplier` optional)
- [ ] `premium` populated for **every** row (`premium_source` optional — defaults to `premium`)
- [ ] `src_bid`, `src_ask`, `src_close` columns present (blank cells allowed)
- [ ] no `forward` column needed (only for a weekly VIX option: its VIX future)
- [ ] `expiry` is `YYYY-MM-DD` and in the future
- [ ] read back as text: CUSIPs that are all digits keep their leading zeros
