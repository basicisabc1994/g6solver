"""Real-disk exception-name callbacks must not hold the ownership lock."""
from pathlib import Path

_retained = Path(__file__).resolve().parents[1] / "_review_rfu6_1" / "test_evidence.py"
_source = _retained.read_text(encoding="utf-8-sig").split("@pytest.mark.parametrize", 1)[0]
_source = _source.replace(".bt_rev_rfu6_1", ".bt_rev_rfu6_3")
exec(compile(_source, str(_retained), "exec"), globals())


@pytest.mark.parametrize("exotic", [False, True], ids=["plain-control", "string-subclass"])
def test_exception_name_comparison_outside_lock(env, exotic):
    (env.disk / "rfu61_server.py").write_text(
        "from g6.mcp import dependencies as deps\n"
        "from threading import Event, Thread\n"
        "WORKERS = []\n"
        "BLOCKED = []\n"
        "class MissingName(str):\n"
        "    __hash__ = str.__hash__\n"
        "    def split(self, sep=None, maxsplit=-1):\n"
        "        return [self]\n"
        "    def __eq__(self, other):\n"
        "        done = Event()\n"
        "        def read_ownership():\n"
        "            deps._ownership_snapshot()\n"
        "            done.set()\n"
        "        worker = Thread(target=read_ownership, daemon=True)\n"
        "        WORKERS.append(worker)\n"
        "        worker.start()\n"
        "        if not done.wait(1):\n"
        "            BLOCKED.append(deps._ownership_lock._is_owned())\n"
        "            raise TimeoutError('exception-name callback blocked on ownership lock')\n"
        "        return str.__eq__(self, other)\n"
        "def build():\n"
        f"    name = MissingName('{OPTIONAL}') if {exotic!r} else '{OPTIONAL}'\n"
        "    raise ModuleNotFoundError('absent optional dependency', name=name)\n",
        encoding="utf-8")
    invalidate_caches()
    server = import_module("rfu61_server")
    try:
        try:
            result = env.families.describe("load_state")
        except TimeoutError as failure:
            assert str(failure) == "exception-name callback blocked on ownership lock"
            assert server.BLOCKED == [True]
            outcome = "blocked-on-ownership-lock"
        else:
            assert result["available"] is False and "install" in result
            outcome = "optional-install"
    finally:
        for worker in server.WORKERS:
            worker.join(2)
        assert all(not worker.is_alive() for worker in server.WORKERS)
    assert outcome == "optional-install", outcome
