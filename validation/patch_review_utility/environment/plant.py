# SPDX-License-Identifier: Apache-2.0
"""The development check's planted change, run by the image's own Python inside a throwaway container of a V7
image (``dev_check.py``): one exact text replacement in one file of ``/app``, printed as a diff against ``HEAD``.
The container is removed afterwards; the diff is applied later by ``apply.sh``, as a patch would be.

    python plant.py <control.json>     (control: {"file", "old", "new"}; "old" must occur exactly once)
"""
import json
import subprocess
import sys


def main(argv):
    with open(argv[1], encoding="utf-8") as handle:
        control = json.load(handle)
    path = "/app/" + control["file"]
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    if text.count(control["old"]) != 1:
        sys.stderr.write("the planted change's text is not in %s exactly once\n" % control["file"])
        return 3
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text.replace(control["old"], control["new"]))
    done = subprocess.run(["git", "-C", "/app", "diff", "--no-color", "--", control["file"]], stdout=subprocess.PIPE,
                          universal_newlines=True, check=True)
    sys.stdout.write(done.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
