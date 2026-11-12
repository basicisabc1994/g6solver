"""Instruments of review 4 of PR #8's fourth follow-up (fakes only: no model, no network, no keys)."""
from contextlib import contextmanager
import sys
import tracemalloc


def peak_of(call):
    """What ``call`` answered (the refusal's text, or what it returned) and the most memory, in bytes, that was
    allocated while it ran. The value handed to ``call`` is built before this starts, so it is not counted."""
    tracemalloc.start()
    try:
        try:
            answer = call()
        except ValueError as exc:
            answer = str(exc)
        return answer, tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()


def count_reaches(monkeypatch):
    """How many values the walker reaches (every call of it, its own recursion included)."""
    from g6.cognitive_bridge import snapshot
    real = snapshot._walk
    count = [0]

    def counting(*args, **kwargs):
        count[0] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(snapshot, '_walk', counting)
    return count


def contains_text(value, text):
    if type(value) is str:
        return value == text
    if type(value) is dict:
        return any(contains_text(v, text) for v in value.values())
    if type(value) is list:
        return any(contains_text(v, text) for v in value)
    return False


def watch_encodings(monkeypatch, text):
    """Every ``json.dumps`` the boundary makes of a value that holds ``text``."""
    from g6.cognitive_bridge import snapshot
    dumps = snapshot.json.dumps
    encoded = []

    def observed(value, *args, **kwargs):
        if contains_text(value, text):
            encoded.append('encoded')
        return dumps(value, *args, **kwargs)

    monkeypatch.setattr(snapshot.json, 'dumps', observed)
    return encoded


@contextmanager
def writer_before_the_take(first, write):
    """Stand-in for another thread that writes to the caller's value after the entry admitted it and before the
    walker takes it: the moment the walker is first called on ``first`` (the caller's outer value), ``write()`` runs,
    once. Deterministic, where a real second thread would only sometimes land in the same window."""
    from g6.cognitive_bridge import snapshot
    previous = sys.getprofile()
    state = {'done': False}

    def profile(frame, event, answer):
        if (event == 'call' and not state['done'] and frame.f_code is snapshot._walk.__code__
                and frame.f_locals.get('value') is first):
            state['done'] = True
            write()

    try:
        sys.setprofile(profile)
        yield state
    finally:
        sys.setprofile(previous)


def claim(name='c', models=None):
    from g6.cognitive_bridge import ClaimIR
    return ClaimIR(name, 's', ('formal',), {'formal': {'formula': 'A'}} if models is None else models)


def raw_claim(name='c', models=None):
    return {'id': name, 'statement': 's', 'evidence_needs': ['formal'],
            'models': {'formal': {'formula': 'A'}} if models is None else models}


def row_model(rows, columns=6):
    """A declarative predictive model: ``rows`` rows of ``columns`` numbers each, as adapt_sklearn takes them."""
    data = [{f'f{j}': float(i + j) for j in range(columns)} for i in range(rows)]
    return {'rows': data, 'target': 'f0', 'features': [f'f{j}' for j in range(1, columns)], 'task': 'regression',
            'seed': 0, 'estimator': 'ridge'}


def predictive_inputs(rows):
    from g6.cognitive_bridge import ClaimIR, Criterion, DomainStandard, MethodVariant, ProblemSignature
    model = row_model(rows)
    return dict(
        claim=ClaimIR('claim', 'A predicts B', ('predictive',), {'predictive': model}),
        context=ProblemSignature('review', 'logic', 'validity', 1, 2),
        standard=DomainStandard('review', '1', 'AU', 'rubric', 'a' * 64,
            (Criterion('c', 'predictive obligation', 'checker', '1:1', required_methods=('predictive',)),)),
        variants=[MethodVariant('v', 'predictive', 'tabular-features', 'adapt_sklearn', 'fit_eval', 1)],
        variant_models={'v': model},
    )


class FakeRegistry:
    """A registry that runs nothing: it records each call and answers with a small mapping."""

    def __init__(self):
        self.calls = []

    def invoke(self, name, op, params=None, **kwargs):
        self.calls.append((name, op))
        return {'answer': {'ran': False}}
