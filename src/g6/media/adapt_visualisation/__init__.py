# SPDX-License-Identifier: Apache-2.0
"""g6.media.adapt_visualisation — chart the rows you give, and show the file holds every row and only the values given.

An extension in the media family. Charts are drawn by G6's own code: SVG and HTML need nothing installed, PNG needs
the ``image`` extra (Pillow).

    from g6.core.registry import get_registry
    out = get_registry().invoke("adapt_visualisation", "bar", {
        "rows": [{"team": "a", "score": 3}, {"team": "b", "score": 5}], "x": "team", "y": "score"}).unwrap()
    out["artifact"]["path"], out["coverage"]["rows_plotted"]   # an SVG under $G6_HOME/media, 2

What it does:

* six charts: ``line``, ``bar``, ``scatter``, ``histogram``, ``box`` and ``heatmap`` (x, y and value), from the
  columns you name; it never guesses a column;
* a coverage report with every result: rows given and plotted, and each row dropped with its column and reason
  (missing, not a number, not finite). Dropping is refused (``G6_E_MEDIA_CHART_DATA_DROPPED``) unless you pass
  ``drop_unplottable``, and then it is reported in the result's message;
* the file is written into G6's media folder and inspected from disk: it decodes, is the size asked for, references
  and runs nothing outside itself, and every value it shows is read back through its drawn axes and compared with
  values worked out from your rows (``G6_E_MEDIA_CHART_MISMATCH`` otherwise); a PNG's values are read from the layout
  it is drawn from, and its marks looked for in the picture. Bars and histogram bins start at a value axis holding
  zero. Results are ``qualified-draft``.

Not ported from the archive's adapt_visualisation, and why:

* the ``verified`` default on its MCP charts, and the placeholder a failed plot was drawn as;
* the heat map that kept a square of one column and dropped the rest, and the guessed columns;
* matplotlib and seaborn (the readback needs geometry G6 controls), the D3 and Plotly pages from adapt_diagrams,
  which loaded scripts from CDNs when opened (the ``html`` format is a page with no script at all);
* the animations, the dataset store, the backend-native spec lane, the chart-recommending model planner, the FastMCP
  server and the scaffold; base64 images in the reply.
"""
from g6.media.adapt_visualisation.block import DATA_DROPPED, AdaptVisualisationBlock, ChartInput
from g6.media.adapt_visualisation.readback import CHART_MISMATCH

__g6_component__ = True
__g6_block__ = "AdaptVisualisationBlock"
__g6_family__ = "media"
__g6_sensitive_ops__: tuple[str, ...] = ()  # nothing reaches the network, starts a program or writes outside G6's folder
__g6_requires__: tuple[str, ...] = ()
# Failure-mode codes (g6.core.failure_modes): what it guards against, and what its own use can cause.
__g6_mitigates__: tuple[str, ...] = ("misleading_chart", "visual_check_failed")
__g6_introduces__: tuple[str, ...] = ()
__g6_outside__: tuple = ()
__g6_extras__: dict[str, tuple[str, ...]] = {"image": ("PIL",)}  # only for PNG; SVG and HTML need nothing

__all__ = ["CHART_MISMATCH", "DATA_DROPPED", "AdaptVisualisationBlock", "ChartInput"]
