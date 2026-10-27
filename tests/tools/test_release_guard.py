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


def test_bundle_workflow_guards_tags_and_does_not_overwrite_or_auto_publish():
    workflow = (ROOT / ".github/workflows/bundles.yml").read_text()
    assert 'python tools/check_release.py "$GITHUB_REF_NAME"' in workflow
    assert "--clobber" not in workflow
    assert "--draft" in workflow and "--verify-tag" in workflow
    assert 'refusing to replace an existing release' in workflow
