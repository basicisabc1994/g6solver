---
name: g6-adapt-visualisation
description: Use when you need a chart of rows of data (a line, bars, a scatter, a histogram, box plots or a heat map) as an SVG, PNG or self-contained HTML file, and need to know it shows every row you gave and only the values you gave, with nothing dropped silently and no script in the file.
---

# G6 adapt_visualisation

`g6.media.adapt_visualisation` charts rows you give, from columns you name, with G6's own drawing code. It writes the
chart into G6's media folder (`$G6_HOME/media`) as SVG, PNG or an HTML page, then inspects the file from disk: it
decodes, is the size asked for, loads and runs nothing, and every value it shows, read back from the file through its
drawn axes, is the value you gave. Every result carries a coverage report of the rows. It is an extension in the media
family. Its best state is `qualified-draft`.

## When to use it

- You (a coding agent) have results as rows and need a picture of them for a report, a README or a person, and you
  cannot look at a chart you wrote code for.
- Some rows may be missing a value or hold text where a number belongs, and you need to know which, not have them
  vanish from the chart.
- You need a bar chart whose bars start at zero, a heat map that keeps every cell, or box plots whose quartiles follow
  a named rule.
- You need a file that is safe to open or share: no script, no link, no style sheet fetched from anywhere.

## When not to use it

- Choosing the chart, the question or the conclusion: nothing here judges whether a chart answers the question asked.
- Dates and times, log scales, stacked or grouped bars, several series in one chart, maps or animation: not here.
- Diagrams of nodes and edges: use adapt_diagrams.
- Very large data: at most 10,000 rows, 200 bars, 1,000 points on a line over categories, 50 box groups and 100 by
  100 heat-map cells.

## Ops

- `line`: `rows`, `x`, `y`. Numbers on x are drawn in order of x (the result's `coverage.notes` says so when that is
  not the order given); categories in the order given. One y per x.
- `bar`: `x` names the category column, `y` the number; one row per category; the value axis always holds zero.
- `scatter`: `x` and `y`, both numbers; one point per row.
- `histogram`: `x`, and `bins` (1 to 50; Sturges' rule when left out). Equal-width bins, each from its lower edge up
  to but not including its upper edge, the last one including it.
- `box`: `y`, and `x` to group by. Quartiles by linear interpolation between order statistics (type 7, NumPy's
  default); whiskers reach the furthest values within 1.5 times the interquartile range (Tukey); the rest are outliers.
- `heatmap`: `x` and `y` name category columns, `value` a number; one cell per pair, coloured by value, with a legend;
  a pair with no row is drawn empty and counted.

Every op also takes `format` (`svg`, the default; `png`; or `html`, a page holding the SVG and a table of what it
shows), `title`, `x_label`, `y_label`, `value_label`, `width` (240 to 2000) and `height` (180 to 2000),
`drop_unplottable`, `name` (G6 chooses the folder) and `overwrite`.

## Alone and composed

Alone over MCP: `g6_invoke("adapt_visualisation", "bar", '{"rows": [...], "x": "team", "y": "score"}')`, then open
`result.artifact.path` and read `result.coverage`. No op is sensitive: nothing reaches the network, starts a program
or writes outside G6's media folder. From Python: `get_registry().invoke("adapt_visualisation", op, params)`.

Composed:

- Rows from a query or a test run go straight in; the coverage report says what was left out and why.
- The `html` page carries its own data table, so a person reading it without the picture still has the numbers.
- The artifact's provenance holds the SHA-256 of the rows, so a stored chart can be matched to the data it came from.

## Failure modes it catches

- `misleading_chart` (a chart that misstates or hides what the data shows):
  - a row whose value is missing, not a number (a boolean, or text that is not a decimal number) or not finite is
    counted and named; the chart is refused with `G6_E_MEDIA_CHART_DATA_DROPPED` unless you pass
    `drop_unplottable`, and then the result's message says how many rows were dropped and why;
  - every value is read back from the written file and compared with values worked out from your rows; a bar at the
    wrong height, a missing or extra category, a point out of place, a bin miscounted, a quartile misplaced, a cell
    in the wrong colour, a value label that disagrees, uneven axis ticks, or bars on an axis without zero are refused
    with `G6_E_MEDIA_CHART_MISMATCH` and the file removed (a file of that name it would have replaced with `overwrite`
    is left as it was);
  - a column is never guessed: one you did not name, or that no row holds, is refused; a category or cell given twice
    is refused, not silently merged, and so are two categories that differ only in white space (`"a b"`, `"a  b"`),
    which look the same when drawn.
- `visual_check_failed` (an image, video or 3D output that is blank, corrupt or wrong, reported as done): the file must
  decode and be the size asked for, a PNG must show each bar, bin, box, cell, point and outlier, and a line's points
  where it has no markers, in its colour where the layout puts it, and a file that would load or run anything outside
  itself is refused with `G6_E_MEDIA_LOADS_EXTERNAL`.

## Reading the result

- `qualified-draft` with `warning_card.code` `G6_E_MEDIA_INSPECTED`; the message names the rows plotted.
- `coverage`: `rows_given`, `rows_plotted`, `dropped_count`, `dropped` (the first twenty, each with its row, column
  and reason), `parsed_from_text`, the columns used and how each was read, and `notes`.
- `chart`: what was drawn (the value axis, the bins and their counts, the box statistics, the cells left empty).
- `artifact`: the file's path, MIME type, size, SHA-256 and each check; evidence holds the read-back's own counts.

## Blind spots

- A PNG's values are read back from the layout it is drawn from, written as SVG, not from its pixels; in the picture,
  its marks are looked for by colour at their places, and its labels are not read. Where no mark is large enough to
  look for (a heat map of cells under 8 pixels, a box of no height), the result's message says the picture was
  checked only for decoding, its size and not being blank.
- A bar or a bin under half a pixel tall cannot be seen; the result's message and `coverage.notes` name each one.
- Numbers are shown to six significant figures where the shortest form is longer.
- The read-back tolerates a fiftieth of a pixel, so values closer than that on a large axis are not told apart; a
  value within that of a bin edge may be counted in either bin.
- Text that looks like a number (`"3.5"`) is read as one, and counted in `parsed_from_text`.
- The read-back does not read the axis titles, the HTML page's data table, how many bins a histogram has or whether
  they are of equal width, two of the three colour channels of a heat-map cell (it reads the one that changes most
  across the legend), or rotated category labels that run off the edge of the picture.
- Nothing judges colour choice, readability or whether the chart suits the question.
- Files stay in the media folder until you delete them.

## What it needs

- Nothing for SVG and HTML.
- `pip install "g6[image]"` (Pillow) for PNG. Without it a PNG is refused with `G6_E_MEDIA_EXTRA_MISSING` and the
  install line before anything is drawn; SVG and HTML still answer.
- No model, no network, no outside program.

## Examples

```g6-example op=bar
{"rows": [{"team": "north", "score": 12}, {"team": "south", "score": 7.5}, {"team": "east", "score": -3}], "x": "team", "y": "score", "title": "Scores by team"}
```

```g6-example op=line
{"rows": [{"week": 1, "open": 30}, {"week": 2, "open": 24}, {"week": 3, "open": 31}, {"week": 4, "open": 18}], "x": "week", "y": "open", "y_label": "open issues"}
```

```g6-example op=scatter
{"rows": [{"size": 10, "ms": 3.1}, {"size": 20, "ms": 5.9}, {"size": 40, "ms": 12.2}, {"size": 80, "ms": 23.8}], "x": "size", "y": "ms"}
```

```g6-example op=histogram
{"rows": [{"ms": 12}, {"ms": 15}, {"ms": 15}, {"ms": 18}, {"ms": 21}, {"ms": 22}, {"ms": 40}], "x": "ms", "bins": 4}
```

```g6-example op=box
{"rows": [{"run": "a", "ms": 10}, {"run": "a", "ms": 12}, {"run": "a", "ms": 11}, {"run": "b", "ms": 20}, {"run": "b", "ms": 25}, {"run": "b", "ms": 90}], "x": "run", "y": "ms"}
```

```g6-example op=heatmap
{"rows": [{"day": "Mon", "hour": "9", "calls": 4}, {"day": "Mon", "hour": "10", "calls": 9}, {"day": "Tue", "hour": "9", "calls": 6}], "x": "day", "y": "hour", "value": "calls", "format": "html"}
```

From Python: a row with no value stops the chart until you say to drop it, and then the drop is reported.

```python
from g6.core.registry import get_registry

reg = get_registry()
rows = [{"k": "a", "v": 3}, {"k": "b", "v": None}, {"k": "c", "v": 5}]
held = reg.invoke("adapt_visualisation", "bar", {"rows": rows, "x": "k", "y": "v"}).unwrap()
assert held["code"] == "G6_E_MEDIA_CHART_DATA_DROPPED" and held["coverage"]["dropped"][0]["row"] == 1
drawn = reg.invoke("adapt_visualisation", "bar", {"rows": rows, "x": "k", "y": "v",
                                                  "drop_unplottable": True}).unwrap()
assert drawn["completion_state"] == "qualified-draft" and drawn["coverage"]["rows_plotted"] == 2
assert "1 dropped" in drawn["warning_card"]["message"]
```

## The four open problems

- **Learning signals**: each refusal names the rows dropped and why, or the value that did not read back. Not yet:
  nothing records them across calls or learns from them.
- **Evaluators the model cannot influence**: the read-back parses the file from disk and recomputes what it should show
  from the rows (box statistics through the standard library's `statistics.quantiles`, a second implementation of
  the drawing's rule); no model is consulted and the caller cannot skip a check.
- **Transfer by failure mode**: dropped data and misdrawn values share two codes across every chart kind, and the
  file checks share the media family's codes.
- **Enough held-out cases**: Not yet: there is no fixed set of datasets with stored charts; the tests check known
  values (quartiles, bin counts), deliberately misdrawn charts in every format and hostile text in labels.
