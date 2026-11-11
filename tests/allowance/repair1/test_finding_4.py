"""A slow observer that never reads stdin must remain inside its deadline."""
import subprocess
import sys
import time


def test_windows_observer_deadline_includes_blocked_stdin_write(monkeypatch):
    from g6._allowance import statusline

    real_popen = subprocess.Popen
    started = []

    def slow_observer(args, **kwargs):
        child = real_popen([sys.executable, '-c', 'import time; time.sleep(2)'], **kwargs)
        started.append(child)
        return child

    monkeypatch.setattr(statusline.subprocess, 'Popen', slow_observer)
    before = time.monotonic()
    try:
        statusline.observe_in_worker(b' ' * statusline.MAX_INPUT, 'p', '2.1.319')
        elapsed = time.monotonic() - before
    finally:
        for child in started:
            if child.poll() is None:
                child.kill()
            child.wait()
    assert elapsed < 1.25, f'observer took {elapsed:.3f}s despite its 0.75s budget'
