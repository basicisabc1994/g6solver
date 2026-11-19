# SPDX-License-Identifier: Apache-2.0
from types import SimpleNamespace

from g6.media.adapt_ffmpeg import block
from g6.media.adapt_diagrams import dot
from g6.core.html import svg_blocks


def test_loudness_json_with_nested_metadata_and_braces(monkeypatch):
    measured = b'log\n{"input_i":"-20", "input_tp":"-3", "metadata":{"note":"a } brace"}}\n'
    monkeypatch.setattr(block, "_ffmpeg", lambda *args, **kwargs: SimpleNamespace(
        code=0, err=measured, text=lambda n: "measurement log"))
    data = SimpleNamespace(target_lufs=-16, true_peak=-1, lra=11)
    found = block._loudness("unused", "input.wav", data)
    assert found["input_i"] == "-20" and found["input_tp"] == "-3"


def test_svg_in_comment_is_not_a_chart():
    assert svg_blocks("<!-- <svg>fake</svg> --><svg width='2' height='3'></svg>") == [
        "<svg width='2' height='3'></svg>"]


def test_graphviz_public_doctype_is_removed_without_losing_namespaces():
    source = (b'<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" '
              b'"http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd">'
              b'<svg xmlns="http://www.w3.org/2000/svg"><g class="node"><title>a</title><ellipse/></g></svg>')
    cleaned = dot.without_doctype(source)
    assert b"DOCTYPE" not in cleaned
    assert dot.drawn(cleaned.decode()) == (["a"], [])
