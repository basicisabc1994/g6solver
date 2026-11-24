"""Review 2 of claude/template-methods-fu (970da2d3), finding 3.

Class: untrusted input (a value's display text) choosing how long G6's own process runs; a guard with no bound.

Repair 1 admits a generated check's failure only when its first line matches the compiled producer grammar
(``verification_templates.failure_matches``, called by ``verification_search.reading`` in G6's own process, after the
confined run has ended and outside every time limit). The grammar is five regular expressions per template, one to
five summary fragments joined by ``; ``, each fragment an alternation whose value-display slots are ``[^\\r\\n]*?``.
A line that holds failure fragments inside its displays can be split in a number of ways that grows as a high power
of the fragments it holds (up to fifteen nested lazy slots), and ``re.fullmatch`` tries them all before it gives up.

(a) Honest producer: two inputs whose returned values display as fragments (under 200 characters each, the
producer's own bound) and three inputs that raise. The line is G6's own ``_fail`` summary, 927 characters; the
reader takes 13 to 15 seconds for it on this machine, against milliseconds for ordinary values.
(b) A ``__repr__`` that raises pytest's own ``Failed`` (a BaseException, which ``_shown`` does not catch) with a
974-character line of fragments: the line is the check's recorded failure, it matches no pattern, and the reader
does not return (24 fragments; each two more multiply the time by about five: 10 take 0.06 s, 12 take 0.34 s,
14 take 1.7 s, measured with ``probe_backtrack2.py``; the producer's record holds 2,000 characters, 48 fragments).

Each reading is made from a real pytest process on the compiled check with G6's own ``_Counted`` plugin; the
reading itself runs in a process of its own (``tmfu2_read.py``) so the test can stop waiting.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import tmfu2_support as support  # noqa: E402

STATED = "def run(value):\n    return value\n"
NODE = "test_g6_relation.py::test_relation"
FRAGMENT = "input 0: a gave b where c was expected"
#: One 181-character display: five failure fragments, as a returned value's repr.
DISPLAY = " where  was expected; input 0:  gave " * 5 + "z"
HONEST = '''
class Odd:
    def __repr__(self):
        return %r
def run(value):
    if isinstance(value, int) and value >= 2:
        raise ValueError("no")
    return Odd()
''' % DISPLAY
RAISES = '''
class Odd:
    def __repr__(self):
        import pytest
        pytest.fail(%r)
def run(value):
    return Odd()
'''
ORDINARY = '''
class Odd:
    def __repr__(self):
        return "an odd value"
def run(value):
    if isinstance(value, int) and value >= 2:
        raise ValueError("no")
    return Odd()
'''
BINDINGS = {"function": "subject.run", "relation": "idempotence", "inputs": [0, 1, 2, 3, 4]}


def _read(request, source, limit, commit=None):
    """G6's reading of a real pytest run of the compiled relation check on ``source``: the helper's line, or None
    when the reading had not returned after ``limit`` seconds (the helper is then stopped). With ``commit``, the
    reader is the one that commit holds."""
    disk = support.disk_for(request.node.nodeid)
    run, record, ran = support.real_pytest(disk / "tree", "relation.v1", "subject", STATED, source, BINDINGS)
    message = record.get("messages", {}).get(NODE, "")
    saved = disk / "run.json"
    saved.write_text(json.dumps(run), encoding="utf-8")
    helper = subprocess.Popen([sys.executable, "-B", str(Path(__file__).parent / "tmfu2_read.py"), str(saved), NODE,
                               *([commit] if commit else [])],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=dict(os.environ))
    try:
        out, err = helper.communicate(timeout=limit)
    except subprocess.TimeoutExpired:
        helper.kill()
        helper.communicate()
        return None, message
    assert helper.returncode == 0, err
    return json.loads(out), message


def test_the_reader_returns_promptly_for_a_line_the_producer_wrote_from_displayed_values(request):
    read, message = _read(request, HONEST, 120)
    first = message.splitlines()[0]
    assert first.startswith("Failed: input 0: idempotence gave ") and first.endswith("; input 4 raised ValueError")
    assert read is not None and read["seconds"] < 2.0, (
        f"the compiled check's own summary line ({len(first)} characters: two inputs whose values display as "
        "failure fragments, three inputs that raise) kept G6's reader in re.fullmatch for "
        f"{'more than 120' if read is None else read['seconds']} seconds; nothing bounds it: {read}")


def test_the_reader_returns_for_a_failure_line_a_repr_raised(request):
    line = "; ".join([FRAGMENT] * 24) + " and other words"
    read, message = _read(request, RAISES % line, 30)
    assert message.splitlines()[0] == "Failed: " + line, message[:200]
    assert read is not None, (
        f"a returned value's __repr__ raised pytest's Failed with a {len(line)}-character line of failure "
        "fragments; it is the generated check's recorded failure, and G6's reading of the run (failure_matches, "
        "in G6's own process, after the run and outside its time limit) had not returned after 30 seconds")
    assert read["result"] == "error", read


# --- controls ----------------------------------------------------------------------------------------------------------

def test_control_the_same_failures_with_ordinary_displays_are_read_at_once(request):
    read, message = _read(request, ORDINARY, 60)
    assert read is not None and read["result"] == "failed" and read["seconds"] < 2.0, read
    assert read["detail"].startswith("Failed: input 0: idempotence gave an odd value where an odd value was expected")


def test_control_the_reader_before_the_repair_read_both_runs_at_once(request):
    honest, _ = _read(request, HONEST, 60, "9203d9c6")
    assert honest is not None and honest["result"] == "failed" and honest["seconds"] < 2.0, honest
    line = "; ".join([FRAGMENT] * 24) + " and other words"
    raised, _ = _read(request, RAISES % line, 60, "9203d9c6")
    assert raised is not None and raised["seconds"] < 2.0, raised


def test_control_a_short_line_no_producer_wrote_is_refused_at_once(request):
    line = "; ".join([FRAGMENT] * 4) + " and other words"
    read, message = _read(request, RAISES % line, 60)
    assert message.splitlines()[0] == "Failed: " + line
    assert read is not None and read["seconds"] < 2.0 and read == {
        "result": "error", "detail": "G6_E_CHECK_SETUP: the generated check recorded no compiled failure line",
        "seconds": read["seconds"]}, read
