"""Why two renders of one reply could differ: the report states the seconds since the review started.

``patch_review._reply`` reads the clock (``time.monotonic() - started``) and the report prints it to a tenth of a
second, so two renders of the same arguments differ in that one text whenever a tenth-of-a-second boundary falls
between them. The frozen-renderer differentials render twice and compare every text; they now read one fixed instant
for both renders, as they already fix the request-id source. Shown here with a clock the test sets, never a wait.
"""
import ast
import copy
import inspect
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

from g6.harness import patch_review
from tests.repair_question2.helpers import REQUIREMENTS, TASK, repo, stated
from tests.repair_question5.test_rendering_differential import strings

TESTS = Path(__file__).resolve().parents[1]


def _captured(tmp_path, monkeypatch):
    root = repo(tmp_path)
    stated(monkeypatch)
    captured = []
    original = patch_review._reply

    def capture(*args, **kwargs):
        bound = inspect.signature(original).bind(*args, **kwargs)
        bound.apply_defaults()
        captured.append(bound)
        return original(*args, **kwargs)

    monkeypatch.setattr(patch_review, '_reply', capture)
    patch_review.review_patch(root, task=TASK, requirements=REQUIREMENTS)
    monkeypatch.setattr(patch_review, 'uuid', SimpleNamespace(uuid4=lambda: UUID(int=1)))
    return original, captured[-1]


def test_two_renders_differ_only_in_the_elapsed_seconds_and_only_across_a_tenth(tmp_path, monkeypatch):
    original, bound = _captured(tmp_path, monkeypatch)
    started = bound.arguments['started']

    def rendered(elapsed):
        with monkeypatch.context() as clock:
            clock.setattr(patch_review, 'time', SimpleNamespace(monotonic=lambda: started + elapsed))
            again = copy.deepcopy(bound)
            return dict(strings(original(*again.args, **again.kwargs)))

    first, same_instant, later = rendered(7.049), rendered(7.049), rendered(7.051)
    assert first == same_instant, 'one instant: every text of the two renders is the same'
    differing = {path for path in first if first[path] != later[path]}
    assert differing == {('assessment', 'report')}, differing
    assert '7.0 seconds' in first['assessment', 'report'] and '7.1 seconds' in later['assessment', 'report']
    assert first['assessment', 'report'].replace('7.0 seconds', '7.1 seconds') == later['assessment', 'report']
    within = rendered(7.0499)   # another instant inside the same tenth: no text differs
    assert first == within


def test_inventory_every_test_that_renders_a_captured_reply_twice_fixes_its_clock():
    """From the tests' code: each function that replaces ``patch_review._reply`` to capture its arguments and renders
    them again reads one instant (it replaces ``patch_review.time``), so no boundary can fall between its renders."""
    found = {}
    for path in sorted(TESTS.rglob('test_*.py')):
        tree = ast.parse(path.read_text(encoding='utf-8'))
        for function in (node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)):
            replaced = {ast.literal_eval(call.args[1]) for call in ast.walk(function)
                        if isinstance(call, ast.Call) and ast.unparse(call.func).endswith('.setattr')
                        and len(call.args) == 3 and ast.unparse(call.args[0]) == 'patch_review'
                        and isinstance(call.args[1], ast.Constant)}
            if '_reply' in replaced:
                found[f'{path.relative_to(TESTS).as_posix()}::{function.name}'] = 'time' in replaced
    assert found == {
        'repair_question5/test_rendering_differential.py::test_every_reply_and_report_text_field_matches_frozen_renderer': True,
        'repair_question6/test_reply_inventory.py::test_full_reply_and_report_each_outcome_keep_every_text_leaf': True,
        'repair_question8/test_reply_clock.py::_captured': False,   # its caller sets the clock for each render
    }, found
