# SPDX-License-Identifier: Apache-2.0
"""Build the repository graph's viewer: Understand-Anything's own dashboard (MIT), unchanged, in its demo mode, into
web/static/graph/app/, which the site and its static build serve at /static/graph/app/.

    python tools/build_graph_viewer.py <dashboard>    # the plugin's packages/dashboard folder, with its node_modules

The graph itself is made separately: the plugin's /understand run on the public export (never on another checkout),
then its knowledge-graph.json and meta.json copied to web/static/graph/. Node is needed only here; the output is
committed, so the site builds without it.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GRAPH = ROOT / "web" / "static" / "graph"
BASE = "/static/graph/"
NOTICE = """The viewer in this folder is the dashboard of Understand-Anything
(https://github.com/Lum1104/Understand-Anything), built unchanged in its demo mode.

MIT License

Copyright (c) 2026 Yuxiang Lin
Copyright (c) 2026 Infinite Universe, Inc.

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""


def build(dashboard: Path) -> None:
    env = {**os.environ, "VITE_GRAPH_URL": BASE + "knowledge-graph.json",
           "VITE_DOMAIN_GRAPH_URL": BASE + "domain-graph.json", "VITE_META_URL": BASE + "meta.json"}
    vite = dashboard / "node_modules" / ".bin" / ("vite.cmd" if os.name == "nt" else "vite")
    subprocess.run([str(vite), "build", "--config", "vite.config.demo.ts", "--base", BASE + "app/",
                    "--outDir", str(GRAPH / "app"), "--emptyOutDir"], cwd=dashboard, env=env, check=True)
    (GRAPH / "app" / "knowledge-graph.json").unlink(missing_ok=True)    # the plugin's own sample, from its public/
    (GRAPH / "app" / "LICENSE.txt").write_text(NOTICE, encoding="utf-8")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    build(Path(sys.argv[1]).resolve())
