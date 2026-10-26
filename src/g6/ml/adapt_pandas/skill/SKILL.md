---
name: g6-adapt-pandas
description: Use when you are handed a table (CSV text, JSON rows or a list of rows) and need to know, before you compute anything from it, which columns are numbers, text or a mix, which values only look missing, which codes have leading zeros, and which rows or keys repeat, read by one stated rule with nothing converted or dropped silently.
---

# G6 adapt_pandas

`g6.ml.adapt_pandas` reads a table you give and describes it. It reads CSV text with a header line, JSON text (an
array of objects) or rows, by one rule it reports in every result, and says what it found: each column's kind,
missing values, numbers mixed with text, values that look like missing markers, numbers written with a thousands
separator or a decimal comma, codes with leading zeros, repeated rows and repeated keys. Nothing is read from a file
and nothing is installed: G6 reads the table with its own code. It is an extension in the ml family. Its best state is
`qualified-draft`.

## When to use it

- Before you (a coding agent) sum, join, chart or model a table someone gave you, and you have not looked at every
  value.
- A column should be numbers and you need to know whether a stray `n/a`, `-` or `12,5` is hiding in it.
- A column is an identifier (a zip code, an account number) and must keep its leading zeros.
- You are about to join on a key and need to know whether it repeats, which would multiply rows.
- You need rows typed one stated way to hand to adapt_sklearn or adapt_automl.

## When not to use it

- Transforming the table (filtering, sorting, grouping, joining, pivoting): not here; do it in your own code once
  the profile says the table is what you think.
- Reading a file from disk, a database or a URL: give the text or the rows; use the database extension for a
  database.
- Very large tables: at most 5,000,000 characters of text, 20,000 rows and 200 columns.
- Judging whether the values are true, in the right units or from the right source: nothing here can know.

## Ops

- `load`: the table (`csv`, `json_text` or `rows`) read by the rule; returns `rows`, each column's kind with its
  present and missing counts, the line accounting (lines read, blank lines skipped, rows out) and the warnings.
- `profile`: the same reading, then per column its kind, missing count and share, distinct values, a number summary
  (count, min, max, mean, median, standard deviation, zeros, negatives) or its most common texts, booleans and notes;
  for the table, repeated rows, repeated values of the `key` columns you name, constant columns and the warnings.

Both take `delimiter` (comma, semicolon, tab or bar) and `missing`, the tokens read as no value in CSV text (by
default only the empty field). A value that looks like a missing marker is reported, never converted unless you list
it.

## Alone and composed

Alone over MCP: `g6_invoke("adapt_pandas", "profile", '{"csv": "...", "key": ["id"]}')`, then read
`result.profile.warnings`. No op is sensitive: nothing reaches the network, reads a file or starts a program. From
Python: `get_registry().invoke("adapt_pandas", op, params)`.

Composed:

- `load`'s `rows`, cut to the target and the features, go to adapt_sklearn's `fit_eval` or `rank`, which refuse a
  mixed feature column and point back here.
- A profile before a join says whether the key repeats; one after it says whether rows multiplied.
- The result's evidence carries the SHA-256 of the table as read, so a later step can show it used the same rows.

## Failure modes it catches

- `data_handling_error` (data mistakes from ad hoc table handling):
  - a column of numbers with a stray text value is `mixed`, and the warning names how many text values sit among how
    many numbers and shows them;
  - values that often stand for no value (`NA`, `null`, `-`, `?`) are named with their counts and kept as text, so
    nothing is treated as missing that you did not say was missing;
  - numbers with a thousands separator (`1,234`) or a decimal comma (`12,5`) are named and kept as text;
  - a column of digits where any value starts with a zero followed by a digit (`02134`) is text, kept as written;
  - numbers given as text in JSON (`"12"`) are named;
  - rows that repeat an earlier row exactly, and key values that appear on more than one row, are counted with the
    first ones shown;
  - a line with more or fewer fields than the header, a column with no name, a name used twice, broken quoting and
    a JSON object that gives a key twice are refused with where, never repaired.

## Reading the result

- `qualified-draft` with `warning_card.code` `G6_E_ML_PROFILED`; the message names the size and the first warnings.
- `profile.warnings`: one sentence per finding, in column order, then the table's.
- `evidence[0]`: the reading rule, the missing tokens used, the line accounting and the table's SHA-256.
- A refusal is `blocked-escalated` with `G6_E_ML_INPUT_INVALID` or `G6_E_ML_TOO_LARGE` and the reason.

## Blind spots

- Units, scales and meaning: a price in cents beside one in dollars is two numbers to it.
- A key that is unique but wrong (the wrong column joined on), and a join that is wrong although the types match.
- Dates and times are text here; nothing parses or checks them.
- Outliers are not judged: the summary shows the minimum and maximum, and you decide.
- Text with surrounding spaces is kept as written (counted in the notes), so `"a"` and `"a "` are two values.

## What it needs

- Nothing to install: the standard library only.
- No model, no network, no outside program, no file.

## Examples

```g6-example op=profile
{"csv": "order,zip,price,status\n1,02134,9.50,paid\n2,90210,n/a,paid\n3,10001,12,NA\n4,02139,\"12,5\",paid\n5,60601,7.25,refunded\n5,60601,7.25,refunded\n", "key": ["order"]}
```

```g6-example op=load
{"csv": "x,y\n1,2.5\n2,4.1\n3,\n", "missing": [""]}
```

From Python: a stray `n/a` makes a column mixed until you say it means no value.

```python
from g6.core.registry import get_registry

reg = get_registry()
text = "id,amount\n1,10\n2,n/a\n3,12.5\n"
found = reg.invoke("adapt_pandas", "profile", {"csv": text}).unwrap()
amount = next(c for c in found["profile"]["columns"] if c["name"] == "amount")
assert amount["kind"] == "mixed" and amount["texts"]["count"] == 1
loaded = reg.invoke("adapt_pandas", "load", {"csv": text, "missing": ["", "n/a"]}).unwrap()
assert loaded["rows"][1]["amount"] is None and loaded["completion_state"] == "qualified-draft"
```

## The four open problems

- **Learning signals**: each warning names its column and its count, so a caller can record what a table got wrong.
  Not yet: nothing records them across calls or learns from them.
- **Evaluators the model cannot influence**: the reading rule is code, stated in every result, and every count is
  recomputed from the values read; no model is consulted and the caller cannot skip a check.
- **Transfer by failure mode**: the same findings apply to every table, and the family's fitting extensions refuse
  a mixed or empty feature column before anything is fitted.
- **Enough held-out cases**: not a measurement, so no held-out set applies. Not yet: there is no fixed set of real
  tables with known faults; the tests use tables built with each fault (stray text, decimal commas, codes, repeated
  rows and keys, ragged lines).
