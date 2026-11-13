"""The array admission reads a buffer's shape and byte count, not its item format.

Review 2's rule 1: the admission bounds the converter that will run, before the conversion starts. The count it admits
is the shape's (``1 + n`` for one dimension). An item of a structured format is not one value: numpy's ``tolist`` writes
each record as a tuple of its fields, so a two-record array of 60,000 one-byte fields is admitted as 3 reaches and
120,000 bytes and converts to 120,003 values before the boundary refuses them (up to the 4 MB byte cap: about four
million values against a visit budget of 100,000). And a native memoryview whose struct format ``tolist`` does not
implement is admitted, then leaves the bridge as NotImplementedError. The commit says a real numpy array is not checked.
"""
import ctypes

import pytest

from g6.cognitive_bridge import extract_tree, snapshot, xai


def _estimator(value):
    class Estimator:
        tree_ = None
        n_features_in_ = 1
        feature_names_in_ = value

    return Estimator()


def test_a_structured_array_is_not_converted_to_more_values_than_the_visit_budget(monkeypatch):
    numpy = pytest.importorskip('numpy')
    names = numpy.zeros(2, dtype=[(f'f{i}', 'i1') for i in range(60_000)])
    assert memoryview(names).shape == (2,) and memoryview(names).nbytes == 120_000  # what the admission reads
    made = []
    taken = xai.plain

    def count(value):
        return 1 + sum(count(item) for item in value) if type(value) in (list, tuple) else 1

    def observed(value, **kwargs):
        made.append(count(value))
        return taken(value, **kwargs)

    monkeypatch.setattr(xai, 'plain', observed)
    with pytest.raises(ValueError):
        extract_tree(_estimator(names))
    assert all(size <= snapshot.MAX_ITEMS for size in made), (
        f'the admitted conversion made {max(made)} values: the visit budget is {snapshot.MAX_ITEMS}')


def test_a_buffer_whose_format_the_native_converter_does_not_read_is_refused_by_the_boundary():
    class Pair(ctypes.Structure):
        _fields_ = [('x', ctypes.c_int), ('y', ctypes.c_double)]

    raised = None
    try:
        extract_tree(_estimator(memoryview((Pair * 2)())))
    except Exception as exc:  # the kind of what is raised is what is under test
        raised = exc
    assert isinstance(raised, ValueError), f'an admitted buffer left the bridge as {raised!r}, not as a refusal'
