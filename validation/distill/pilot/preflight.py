"""Check local image identities and network-off execution without acquiring images."""
from __future__ import annotations

import json
import subprocess

from . import access


def docker_runner(command):
    return subprocess.run(command, capture_output=True, text=True, encoding="utf-8", timeout=120, check=False)


def invoke(runner, command):
    try:
        return runner(command)
    except (OSError, subprocess.SubprocessError):
        return subprocess.CompletedProcess(command, 1, "", "runner unavailable")


def run_command(image_id: str, executable: str, *args: str) -> list[str]:
    return ["docker", "run", "--rm", "--pull", "never", "--network", "none", "--read-only",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--entrypoint", executable,
            image_id, *args]


def image_checks(bug: dict, runner) -> list[dict]:
    rows = []
    for kind in ("task", "oracle"):
        name, expected = bug.get(f"{kind}_image"), bug.get(f"{kind}_image_id")
        row = {"kind": kind, "reason_code": "ok"}
        if not isinstance(name, str) or not name or name.startswith("-") or not isinstance(expected, str):
            row["reason_code"] = "image_metadata_invalid"
        elif len(expected) != 71 or not expected.startswith("sha256:") or any(
                c not in "0123456789abcdef" for c in expected[7:]):
            row["reason_code"] = "image_metadata_invalid"
        else:
            got = invoke(runner, ["docker", "image", "inspect", name, "--format", "{{.Id}}"])
            if got.returncode:
                row["reason_code"] = "image_missing"
            elif got.stdout.strip() != expected:
                row["reason_code"] = "image_id_mismatch"
            else:
                row["image_id"] = expected
        rows.append(row)
    return rows


def inspect_bug(manifest, cohort_path, bug_id, *, runner=docker_runner) -> dict:
    bug = access.load_bug(manifest, cohort_path, bug_id)
    daemon = invoke(runner, ["docker", "info", "--format", "{{.ServerVersion}}"])
    if daemon.returncode:
        return {"bug_id": bug_id, "ok": False, "reason_codes": ["daemon_down"], "images": []}
    rows = image_checks(bug, runner)
    for row in rows:
        if row["reason_code"] == "ok":
            result = invoke(runner, run_command(row["image_id"], "/bin/true"))
            if result.returncode:
                row["reason_code"] = "network_off_run_failed"
    codes = sorted({row["reason_code"] for row in rows} - {"ok"})
    return {"bug_id": bug_id, "ok": not codes, "reason_codes": codes, "images": rows}


def main(argv=None, *, runner=docker_runner) -> int:
    parser = access.arguments(__doc__)
    args = parser.parse_args(argv)
    try:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        ids = access.selected(manifest, args.bug)
        rows = [inspect_bug(manifest, args.cohort, bug, runner=runner) for bug in ids]
        access.write_json(args.output, {"bugs": rows})
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(2, f"{exc}\n")
    return 0 if all(row["ok"] for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
