# SPDX-License-Identifier: Apache-2.0
import importlib.util
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "export_public", Path(__file__).resolve().parents[2] / "tools" / "export_public.py"
)
export_public = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(export_public)


def _fake(n: int) -> str:
    return "A" * n  # built at runtime so no secret-shaped literal sits in this file


def test_clean_tree_has_no_findings(tmp_path: Path) -> None:
    (tmp_path / "ok.py").write_text("print('hello')\n")
    assert export_public.scan(tmp_path) == []


def test_secret_reported_without_value(tmp_path: Path) -> None:
    secret = "sk-or-v1-" + _fake(40)
    (tmp_path / "leak.py").write_text(f"KEY = '{secret}'\n")
    findings = export_public.scan(tmp_path)
    assert findings == ["leak.py:1: openrouter-key"]
    assert secret not in "".join(findings)


def test_pii_and_denied_files(tmp_path: Path) -> None:
    (tmp_path / "notes.md").write_text("see " + "C:" + "\\Users\\someone\\x\n")
    (tmp_path / "gpqa_diamond_cache.json").write_text("{}")
    (tmp_path / "book.pdf").write_bytes(b"%PDF")
    findings = export_public.scan(tmp_path)
    assert "notes.md:1: windows-user-path" in findings
    assert any(f.startswith("gpqa_diamond_cache.json: denied-file") for f in findings)
    assert "book.pdf: pdf-outside-theory" in findings


def test_theory_pdf_allowed(tmp_path: Path) -> None:
    d = tmp_path / "docs" / "theory"
    d.mkdir(parents=True)
    (d / "paper.pdf").write_bytes(b"%PDF")
    assert export_public.scan(tmp_path) == []


def _email() -> str:
    return "someone" + "@gmail.com"  # built at runtime, like _fake


def test_waiver_covers_one_rule_in_one_exact_file(tmp_path: Path) -> None:
    f = tmp_path / "calls.jsonl"
    f.write_bytes((_email() + "\r\n" + "C:" + "\\Users\\x\r\n").encode())
    waived = {("calls.jsonl", export_public.content_sha256(f), "personal-email")}
    assert export_public.scan(tmp_path, waived) == ["calls.jsonl:2: windows-user-path"]  # the other rule still fires
    (tmp_path / "other.jsonl").write_text(f"{_email()}\n")
    assert "other.jsonl:1: personal-email" in export_public.scan(tmp_path, waived)  # another file is not covered
    f.write_bytes(f"{_email()}\nchanged\n".encode())
    assert "calls.jsonl:1: personal-email" in export_public.scan(tmp_path, waived)  # an edit brings it back


def test_waiver_digest_ignores_line_endings(tmp_path: Path) -> None:
    (tmp_path / "a").write_bytes(b"x\r\ny\r\n")
    (tmp_path / "b").write_bytes(b"x\ny\n")
    assert export_public.content_sha256(tmp_path / "a") == export_public.content_sha256(tmp_path / "b")


def test_a_secret_rule_can_never_be_waived(tmp_path: Path) -> None:
    exceptions = tmp_path / "SCAN_EXCEPTIONS"
    exceptions.write_text(f"leak.py {'0' * 64} openrouter-key\n")
    with pytest.raises(ValueError, match="cannot be waived"):
        export_public.read_exceptions(exceptions)


def test_the_shipped_waivers_are_pii_only() -> None:
    pii = {rule for rule, _ in export_public.PII_RULES}
    assert export_public.read_exceptions() and {rule for _, _, rule in export_public.read_exceptions()} <= pii


def test_a_file_that_is_not_utf8_is_a_finding(tmp_path: Path) -> None:
    # EXPORT_PUBLIC-01: a file the scan cannot read as text is never passed unscanned
    # (fails while scan() does "except (UnicodeDecodeError, OSError): continue")
    (tmp_path / "notes.txt").write_bytes("caf\u00e9 owner notes".encode("cp1252"))
    (tmp_path / "data.csv").write_bytes("name,value\n".encode("utf-16"))
    findings = export_public.scan(tmp_path)
    assert "notes.txt: not-utf8-text" in findings and "data.csv: not-utf8-text" in findings


def test_a_listed_binary_is_named_with_its_size_in_the_summary(tmp_path: Path, monkeypatch, capsys) -> None:
    # EXPORT_PUBLIC-01: an image, font or archive the export carries is not text; the summary names it and its size
    # (fails while main() prints no "binary:" line, export_public.scan_report)
    png = b"\x89PNG\r\n\x1a\n" + bytes(range(256))
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "logo.png").write_bytes(png)
    (tmp_path / "PII_TERMS").write_text("# no terms\n", encoding="utf-8")
    monkeypatch.setattr(export_public, "PII_TERMS_FILE", tmp_path / "PII_TERMS")
    assert export_public.scan(tree) == []
    assert export_public.scan_report(tree)[1] == [("logo.png", len(png))]
    assert export_public.main(["--scan", str(tree)]) == 0
    assert f"binary: logo.png ({len(png):,} bytes)" in capsys.readouterr().out


def test_a_binary_suffix_does_not_hide_text(tmp_path: Path) -> None:
    # (fails while TEXT_SUFFIX_SKIP skips a .png by its name before reading it)
    (tmp_path / "logo.png").write_text("KEY = 'sk-or-v1-" + _fake(40) + "'\n", encoding="utf-8")
    assert export_public.scan(tmp_path) == ["logo.png:1: openrouter-key"]


@pytest.mark.parametrize("parts", [("", "c", "Users", "kim", "project"), ("", "home", "kim", "project"),
                                   ("", "Users", "kim", "project"), ("C:", "Users", "kim", "project")])
def test_posix_and_git_bash_home_folders_are_found(tmp_path: Path, parts: tuple[str, ...]) -> None:
    # EXPORT_PUBLIC-02: Git Bash, Linux and macOS home folders, built at runtime so this file holds none
    # (fails while PII_RULES knows only the drive-letter form, "windows-user-path"). "kim", not "someone": "someone" is
    # one of the placeholder home names the rule passes (PLACEHOLDER_HOME_NAMES).
    (tmp_path / "notes.md").write_text(f"see {'/'.join(parts)}\n")
    assert len(export_public.scan(tmp_path)) == 1


def test_a_placeholder_or_a_web_address_is_not_a_home_folder(tmp_path: Path) -> None:
    # (fails if the home-folder rules match a placeholder name or a path inside a web address)
    (tmp_path / "notes.md").write_text("cd /home/<name>/ or $HOME; https://example.org/home/someone/x\n")
    assert export_public.scan(tmp_path) == []


def test_a_placeholder_home_name_passes_and_any_other_name_is_found(tmp_path: Path) -> None:
    # Removing export_public._allowed's `if rule == "posix-home-path":` branch makes this fail (the placeholders found).
    names = export_public.PLACEHOLDER_HOME_NAMES
    assert names == {"me", "user", "alice", "bob", "jdoe", "someone"}
    home = "/".join(("", "home", ""))   # built at runtime, like _email
    users = "/".join(("", "Users", ""))
    (tmp_path / "ok.md").write_text("".join(f"cd {home}{n}/x or {users}{n}/y\n" for n in sorted(names)))
    (tmp_path / "real.md").write_text(f"{home}kim/x\n{users}alicia/y\n{home}someone2/z\n{home}Bob/w\n")
    assert export_public.scan(tmp_path) == [f"real.md:{n}: posix-home-path" for n in (1, 2, 3, 4)]


def test_any_email_address_is_found_except_the_named_allowlist(tmp_path: Path) -> None:
    # EXPORT_PUBLIC-02 (fails while the e-mail rule matches only gmail addresses)
    at = "@"   # built at runtime, like _email
    (tmp_path / "a.md").write_text(f"write to someone{at}company.co.uk\n")
    (tmp_path / "b.md").write_text(f"<a href='mailto:someone{at}company.com'>\n")
    (tmp_path / "ok.md").write_text(f"noreply{at}anthropic.com hello{at}g6solver.com a{at}example.com "
                                    f"x{at}sub.example.invalid y{at}clinic.example.org z{at}db.example\n")
    (tmp_path / "code.py").write_text('text = "x\\n@pytest.fixture"; url = "https://user:pw' + at + 'github.com/x"\n')
    assert export_public.scan(tmp_path) == ["a.md:1: personal-email", "b.md:1: personal-email"]


def test_a_run_without_the_private_terms_list_says_so_and_only_public_ci_may_run_it(
        tmp_path: Path, monkeypatch, capsys) -> None:
    # EXPORT_PUBLIC-02 (fails while _private_terms() returns [] silently and main() runs anyway)
    (tmp_path / "ok.py").write_text("print('hello')\n")
    monkeypatch.setattr(export_public, "PII_TERMS_FILE", tmp_path / "missing" / "PII_TERMS")
    assert export_public.main(["--scan", str(tmp_path)]) == 2
    assert "private-terms list" in capsys.readouterr().err
    assert export_public.main(["--scan", str(tmp_path), "--public-ci"]) == 0
    assert "ran without the private-terms list" in capsys.readouterr().out


def test_a_run_with_the_private_terms_list_counts_its_terms_and_never_prints_them(
        tmp_path: Path, monkeypatch, capsys) -> None:
    terms = tmp_path / "PII_TERMS"
    terms.write_text("# a comment\nZebulon Quartermaine\n", encoding="utf-8")
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "a.md").write_text("written by zebulon quartermaine\n")
    monkeypatch.setattr(export_public, "PII_TERMS_FILE", terms)
    assert export_public.main(["--scan", str(tree)]) == 1
    out = capsys.readouterr().out
    assert "a.md:1: private-term" in out and "private terms: 1 from" in out and "zebulon" not in out.lower()


def test_the_public_export_is_clean(tmp_path: Path) -> None:
    # The scan public CI runs (R-08); in the exported repo the allowlist is absent and CI scans the tree directly.
    if not export_public.ALLOWLIST.is_file():
        pytest.skip("no allowlist: this is the exported tree")
    export_public.export(tmp_path / "out")
    assert export_public.scan(tmp_path / "out") == []
