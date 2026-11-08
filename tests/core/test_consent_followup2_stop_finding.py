"""Creating an empty STOP file is not writing the stop's line."""
from pathlib import Path

from g6.mcp import improve
from g6.self_maintenance import envelope


def test_a_stop_that_creates_a_file_then_cannot_write_does_not_claim_its_line(tmp_path, monkeypatch):
    (tmp_path / "settings.json").write_text("{}", encoding="utf-8")
    real_open = open

    class NoRoom:
        def __init__(self, handle):
            self.handle = handle

        def __enter__(self):
            return self

        def write(self, text):
            raise OSError("disk full before the first byte")

        def __exit__(self, *args):
            self.handle.close()

    def opened(path, mode="r", *args, **kwargs):
        handle = real_open(path, mode, *args, **kwargs)
        return NoRoom(handle) if Path(path).name == "STOP" and mode == "a" else handle

    monkeypatch.setattr(envelope, "open", opened, raising=False)
    reply = improve.stop(str(tmp_path), "please stop")
    assert (tmp_path / "STOP").read_bytes() == b""
    assert envelope.stops_asked(tmp_path) == 1
    assert envelope.stop_standing(tmp_path, None)
    assert reply["recorded"]["the stop"] is True
    assert reply["recorded"]["its line in the STOP file"] is False, (
        "stop reports its line on record although STOP contains zero bytes"
    )
