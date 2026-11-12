"""Frozen 68ca0eb entry points; run only in the differential fixture namespace."""
# ruff: noqa: F821
from __future__ import annotations

def reviewed(review: Any, repo: str, task: str, **kwargs: Any) -> Any:
    """A queued ``g6_review_patch``, as its job runs it: the standard the person confirmed for the task read now, when
    the review executes (``enforced``), so an answer given while it waited is the one enforced; a store G6 cannot read
    refuses the review. The reply is bound to the task (``bound``)."""
    from g6.harness import standard_publication
    try:
        confirmed = enforced(repo, task)
        from g6.harness.confirmed_expectation import ExpectationStandard
        identity = identity_of(repo, task)
        store = qs.TaskQuestions(identity) if identity is not None else None
        admitted = confirmed if confirmed is not None else ExpectationStandard()
        from contextlib import nullcontext
        guard = store.counting if store is not None else lambda _standard: nullcontext()
        with standard_publication.observing(guard):
            reply = review(repo, task=task, **{'confirmed': confirmed} if confirmed is not None else {}, **kwargs)
        with guard(admitted):
            return bound(reply, identity)
    except qs.StoreUnreadable as exc:
        return blocked_reply(str(exc))
    except standard_publication.StandardSuperseded as exc:
        return blocked_reply(str(exc))
