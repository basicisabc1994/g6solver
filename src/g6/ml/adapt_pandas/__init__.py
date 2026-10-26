# SPDX-License-Identifier: Apache-2.0
"""g6.ml.adapt_pandas — read a table you give by one stated rule, and say where it mixes kinds, looks missing or repeats.

An extension in the ml family. It needs nothing installed: G6 reads the table with its own code over the standard
library, so every value is read by the one rule it reports, never by a library's guess.

    from g6.core.registry import get_registry
    out = get_registry().invoke("adapt_pandas", "profile", {
        "csv": "id,price\n1,9.5\n2,n/a\n3,12\n", "key": ["id"]}).unwrap()
    out["profile"]["columns"][1]["kind"], out["profile"]["warnings"][0]   # "mixed", "price: 1 text value(s) ..."

What it does:

* ``load`` reads CSV text (with a header line), JSON text (an array of objects) or rows, and returns the rows as read,
  each column's kind and the line accounting (lines read, blank lines skipped, rows out), with the warnings below;
* ``profile`` describes each column (its kind, missing count, distinct values, a number summary or its most common
  texts, and notes) and the table (rows that repeat an earlier row, key values that repeat, constant columns), with
  a warning for each finding that commonly turns into a data mistake: numbers mixed with text, values that look like
  missing markers (``NA``, ``null``, ``-``), numbers with a thousands separator or a decimal comma, codes with leading
  zeros (kept as text), repeated rows and repeated keys.

Only the tokens you name in ``missing`` count as missing (by default only the empty field); a value that looks like a
missing marker is reported, never converted. A line with the wrong number of fields, a column with no name or a name
used twice is refused, never repaired. Results are ``qualified-draft``.

Not ported from the archive's adapt_pandas, and why: pandas itself (its type inference turns ``007`` into ``7`` and
``NA`` into a missing value without saying so; the reader here is small and states its rule); reading files from a
folder (a table is given as text or rows, so nothing outside the call is read); the transforms (dropna, filter, sort,
concat, pivot, groupby, merge) and the stored pipelines, the production gate opened by any reviewer string, the
billing import, the temp-folder read, the model planner, the MCP server and the scaffold.
"""
from g6.ml.adapt_pandas.block import AdaptPandasBlock, TableInput
from g6.ml.adapt_pandas.table import READING_RULE, Table, profile, read_csv, read_json, read_rows

__g6_component__ = True
__g6_block__ = "AdaptPandasBlock"
__g6_family__ = "ml"
__g6_sensitive_ops__: tuple[str, ...] = ()  # nothing reaches the network, reads a file or starts a program
__g6_requires__: tuple[str, ...] = ()
# Failure-mode codes (g6.core.failure_modes): what it guards against, and what its own use can cause.
__g6_mitigates__: tuple[str, ...] = ("data_handling_error",)
__g6_introduces__: tuple[str, ...] = ()
__g6_outside__: tuple = ()
__g6_extras__: dict[str, tuple[str, ...]] = {}  # nothing to install

__all__ = ["READING_RULE", "AdaptPandasBlock", "Table", "TableInput", "profile", "read_csv", "read_json", "read_rows"]
