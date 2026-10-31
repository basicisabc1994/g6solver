"""A public version must identify stable source bytes and immutable release assets."""
from pathlib import Path
import runpy

import pytest


ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("tag,version", [("v0.1.0", "0.1.1"), ("v0.1.1", "0.1.1.dev0"),
                                        ("v0.1.1.dev0", "0.1.1.dev0"), ("0.1.1", "0.1.1")])
def test_wrong_or_development_version_cannot_be_released(tag, version):
    check = runpy.run_path(str(ROOT / "tools/check_release.py"))["check"]
    with pytest.raises(ValueError):
        check(tag, version)


def test_stable_matching_tag_can_be_prepared():
    assert runpy.run_path(str(ROOT / "tools/check_release.py"))["check"]("v0.1.1", "0.1.1") is None


def test_the_launch_notes_name_the_stable_version_g6_reports():
    # the owner set it, 2026-10-29: 0.2.0; the release tag must then be v0.2.0 (tools/check_release.py)
    import g6
    title = (ROOT / "docs/releases/launch.md").read_text(encoding="utf-8").splitlines()[0]
    assert g6.__version__ == "0.2.0" and title.startswith(f"# G6 {g6.__version__}: "), title
    assert runpy.run_path(str(ROOT / "tools/check_release.py"))["check"](f"v{g6.__version__}", g6.__version__) is None


def test_bundle_workflow_guards_tags_and_does_not_overwrite_or_auto_publish():
    workflow = (ROOT / ".github/workflows/bundles.yml").read_text()
    assert 'python tools/check_release.py "$GITHUB_REF_NAME"' in workflow
    assert "--clobber" not in workflow
    assert "--draft" in workflow and "--verify-tag" in workflow
    assert 'refusing to replace an existing release' in workflow


def test_ci_checks_the_exact_reviewed_head_for_history_bound_certificates():
    workflow = (ROOT / ".github/workflows/ci.yml").read_text()
    # GitHub's synthetic PR merge has a different first-parent history. The release
    # plan lands the reviewed stack by fast-forward, so CI must check that exact head.
    assert workflow.count("ref: ${{ github.event.pull_request.head.sha || github.sha }}") == 4
