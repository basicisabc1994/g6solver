# SPDX-License-Identifier: Apache-2.0
"""Build one double-clickable executable per bundle with PyInstaller (run once per OS in CI).

    pip install . pyinstaller
    python packaging/build_bundles.py            # all bundles -> dist/
    python packaging/build_bundles.py safe-actions

Each executable starts the bundle's private local page and opens the browser.
"""
from __future__ import annotations

import platform
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "build" / "bundles"
LAUNCHER = '''from g6.bundles import catalog
from g6.bundles.server import serve

serve(catalog()[{bundle_id!r}])
'''


def artifact_name(bundle_id: str) -> str:
    system = {"Darwin": "macos", "Windows": "windows"}.get(platform.system(), "linux")
    return f"g6-{bundle_id}-{system}-{platform.machine().lower()}"


def build(bundle_id: str) -> Path:
    WORK.mkdir(parents=True, exist_ok=True)
    launcher = WORK / f"launch_{bundle_id.replace('-', '_')}.py"
    launcher.write_text(LAUNCHER.format(bundle_id=bundle_id), encoding="utf-8")
    name = artifact_name(bundle_id)
    subprocess.run([
        sys.executable, "-m", "PyInstaller", "--onefile", "--console", "--noconfirm", "--clean",
        "--name", name, "--distpath", str(ROOT / "dist"), "--workpath", str(WORK / "work"),
        "--specpath", str(WORK), "--collect-submodules", "g6", "--copy-metadata", "g6",
        "--exclude-module", "fastmcp", "--exclude-module", "mcp", "--exclude-module", "tkinter",
        str(launcher),
    ], check=True)
    exe = ROOT / "dist" / (name + (".exe" if platform.system() == "Windows" else ""))
    if not exe.is_file():
        raise SystemExit(f"build produced no executable for {bundle_id}")
    return exe


def main(argv: list[str]) -> int:
    from g6.bundles import catalog

    ids = argv or list(catalog())
    unknown = [i for i in ids if i not in catalog()]
    if unknown:
        raise SystemExit(f"unknown bundle(s): {', '.join(unknown)}")
    for bundle_id in ids:
        print(build(bundle_id))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
