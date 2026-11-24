"""Review 2 helper: G6's reading (``verification_search.reading``) of one recorded run, in a process of its own,
so a caller can stop waiting for it. Prints one JSON line: the reading's result, detail and the seconds it took.
With a commit as the third argument, the reader is ``verification_search`` as that commit holds it (git show)."""
import importlib.util
import json
import subprocess
import sys
import time
from pathlib import Path

from g6.harness import verification_search as vs


def reader_at(commit):
    root = Path(__file__).resolve().parents[2]
    text = subprocess.run(["git", "-C", str(root), "show", commit + ":src/g6/harness/verification_search.py"],
                          capture_output=True, timeout=60, check=True).stdout
    path = Path(__file__).parent / "disk" / ("verification_search_" + commit + ".py")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text)
    spec = importlib.util.spec_from_file_location("tmfu2_verification_search_" + commit, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


if __name__ == "__main__":
    record, node = sys.argv[1:3]
    reader = reader_at(sys.argv[3]) if len(sys.argv) > 3 else vs
    with open(record, encoding="utf-8") as handle:
        run = json.load(handle)
    began = time.perf_counter()
    read = reader.reading(run, node)
    print(json.dumps({"result": read["result"], "detail": read["detail"],
                      "seconds": round(time.perf_counter() - began, 3)}), flush=True)
