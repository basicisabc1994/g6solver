"""The shared intake/rectification setup works without a Windows TMP."""
import tempfile

from tests.distill.test_intake import local_home


def test_intake_home_uses_platform_temp_without_tmp(monkeypatch, tmp_path):
    monkeypatch.delenv("TMP", raising=False)
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    home = local_home.__wrapped__(monkeypatch)
    assert home.parent == tmp_path and home.is_dir()
