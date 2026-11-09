"""Equivalent path spellings must share the delivery copy of their file."""
import os
from pathlib import Path
from uuid import uuid4

from g6.self_maintenance.usage import Request


def test_equivalent_input_spellings_can_share_the_private_copy():
    tmp_path = Path(__file__).resolve().parents[2] / ".review" / "finding-2" / uuid4().hex
    tmp_path.mkdir(parents=True)
    path = tmp_path / "input.json"
    path.write_bytes(b'{"mcpServers": {}, "disableAllHooks": true}')
    equivalent = str(tmp_path) + os.sep + "." + os.sep + path.name
    request = Request.host(["fake-claude", "--mcp-config", str(path), "--settings", equivalent], "user", tmp_path)
    assert len(request.files) == 1
    try:
        try:
            argv, stdin = request.handoff()
        except OSError as error:
            assert False, f"equivalent aliases collide instead of sharing their delivery copy: {type(error).__name__}"
        assert stdin == "user"
        assert Path(argv[2]).read_bytes() == Path(argv[4]).read_bytes() == path.read_bytes()
    finally:
        request.close()
