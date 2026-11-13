"""The cleanup identity check must cover the directory actually deleted."""
from contextlib import contextmanager

from g6.self_maintenance import usage


def test_private_copy_cleanup_does_not_delete_a_replacement(review_host, monkeypatch):
    root, _ = review_host
    source = root / "source"
    source.write_bytes(b"admitted")
    request = usage.Request.host(["fake", "--settings", str(source)], "stdin", root)
    request.handoff()
    target, _ = request._delivery.folders[0]
    saved = root / "saved-private-folder"
    victim = root / "unrelated-folder"
    victim.mkdir()
    (victim / "keep.txt").write_bytes(b"unrelated data")
    original = usage._open_input
    swapped = False

    @contextmanager
    def opening(path, **kwargs):
        nonlocal swapped
        with original(path, **kwargs) as stream:
            yield stream
        # At this point close() has verified the private folder's identity,
        # then dropped its last handle, but has not called rmtree yet.
        if path == target and not swapped:
            try:
                target.rename(saved)
                swapped = True
                victim.rename(target)
            except PermissionError:
                pass  # Keeping custody through cleanup may legitimately prevent the swap.

    monkeypatch.setattr(usage, "_open_input", opening)
    request.close()
    assert (victim / "keep.txt").exists() or (target / "keep.txt").exists(), (
        "cleanup deleted an unrelated directory swapped in after its identity check"
    )
