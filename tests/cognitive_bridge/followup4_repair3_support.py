"""Instruments of review 3 of PR #8's fourth follow-up, brought in unchanged with its tests (fakes only: no model, no network, no keys)."""
from contextlib import contextmanager
import json
import sys


@contextmanager
def observe_walk(predicate):
    """Record every completed detachment (a return of the walker with a value) of what ``predicate`` names."""
    from g6.cognitive_bridge import snapshot
    calls = []
    previous = sys.getprofile()

    def profile(frame, event, answer):
        if event == 'return' and answer is not None and frame.f_code is snapshot._walk.__code__:
            if predicate(frame.f_locals['value']):
                calls.append('walk')

    try:
        sys.setprofile(profile)
        yield calls
    finally:
        sys.setprofile(previous)


@contextmanager
def second_writer(target, first, extra):
    """Stand-in for another thread that appends to the caller's list while the walker copies it: the moment the
    walker reaches ``first`` (the list's first item), ``extra`` is appended to ``target``, once. Deterministic, where a
    real second thread would only sometimes land in the same window."""
    from g6.cognitive_bridge import snapshot
    previous = sys.getprofile()
    state = {'done': False}

    def profile(frame, event, answer):
        if (event == 'call' and not state['done'] and frame.f_code is snapshot._walk.__code__
                and frame.f_locals.get('value') is first):
            state['done'] = True
            target.extend(extra)

    try:
        sys.setprofile(profile)
        yield state
    finally:
        sys.setprofile(previous)


def compile_inputs():
    from g6.cognitive_bridge import ClaimIR, Criterion, DomainStandard, MethodVariant, ProblemSignature
    return dict(
        claim=ClaimIR('claim', 'A entails A', ('formal',), {}),
        context=ProblemSignature('review', 'logic', 'validity', 1, 2),
        standard=DomainStandard('review', '1', 'AU', 'rubric', 'a' * 64,
            (Criterion('c', 'formal obligation', 'checker', '1:1', required_methods=('formal',)),)),
        variants=[MethodVariant('v', 'formal', 'SMT', 'formal', 'check_validity', 1)],
        variant_models={'v': {'formula': 'A', 'premises': ['A']}},
    )


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


def watch_hashes(monkeypatch, planted):
    """Every SHA-256 the boundary takes of the very object ``planted``."""
    from g6.cognitive_bridge import snapshot
    real = snapshot.hashlib.sha256
    hashed = []

    def observed(data=b'', *args, **kwargs):
        if data is planted:
            hashed.append('hashed')
        return real(data, *args, **kwargs)

    monkeypatch.setattr(snapshot.hashlib, 'sha256', observed)
    return hashed


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


class NoModel:
    """A fake model: answers with the JSON it was made with, and makes no call."""

    def __init__(self, reply=None):
        self.reply = {'claims': []} if reply is None else reply

    def complete(self, *args, **kwargs):
        return json.dumps(self.reply)
