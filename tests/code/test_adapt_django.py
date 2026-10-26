# SPDX-License-Identifier: Apache-2.0
"""Generator capabilities and Django regressions, including real optional sandbox checks."""
import ast

import pytest
from pydantic import ValidationError

from g6.code.adapt_django import AdaptDjangoBlock, AdaptDjangoInput
from g6.core.registry import get_registry

SPEC = {"app_name": "catalogue", "project_name": "project",
        "models": [{"name": "Entry", "str_field": "title",
                    "fields": [{"name": "title", "field_type": "CharField", "default": "",
                                "choices": [["", "Empty"], ["ok", "Okay"]], "help_text": "Pick one"}]}],
        "views": [{"name": "EntryList", "model_name": "Entry", "view_type": "list", "filter_fields": ["title"]}]}


def run(op, **kwargs):
    return AdaptDjangoBlock().infer(AdaptDjangoInput(op=op, **kwargs)).unwrap()


def project():
    files = run("build", spec=SPEC)["files"]
    files["manage.py"] = ("import os\nimport sys\nos.environ['DJANGO_SETTINGS_MODULE'] = 'project.settings'\n"
                          "from django.core.management import execute_from_command_line\nexecute_from_command_line(sys.argv)\n")
    files["project/settings.py"] = files["project/settings.py"].replace("SECRET_KEY = ''", "SECRET_KEY = 'test-only'")
    return files


def test_all_seven_generators_through_block():
    out = run("build", spec=SPEC)
    for module in ["models", "views", "urls", "admin", "forms", "serializers"]:
        assert f"catalogue/{module}.py" in out["files"]
    assert "project/settings.py" in out["files"]
    for source in out["files"].values():
        ast.parse(source)
# Removal: generator mapping in build_django.


@pytest.mark.parametrize("field", ["python_path", "base_dir", "directory", "approved"])
def test_adapt_django_01_no_interpreter_directory_or_approval(field):
    with pytest.raises(ValidationError):
        AdaptDjangoInput(op="run_checks", **{field: "anything"})
# Removal: extra=forbid in AdaptDjangoInput.


@pytest.mark.parametrize("bad", [
    {"models": [{"name": "Bad;raise RuntimeError()", "fields": []}]},
    {"models": [{"name": "Entry", "str_field": "x); print('oops')", "fields": []}]},
    {"views": [{"name": "V", "model_name": "Entry", "filter_fields": ["name);evil("]}]},
])
def test_adapt_django_01_identifiers_refused(bad):
    assert run("build", spec=bad)["completion_state"] == "blocked-escalated"
# Removal: Identifier validators in schema.


def test_adapt_django_02_success_is_draft():
    assert run("build", spec=SPEC)["completion_state"] == "qualified-draft"
# Removal: checked_reply ceiling for generated code.


def test_adapt_django_03_options_retained_without_handwritten_migrations():
    out = run("build", spec=SPEC)
    source = out["files"]["catalogue/models.py"]
    assert "default=''" in source and "help_text='Pick one'" in source and "choices=" in source
    assert not any("0001" in name for name in out["files"])
# Removal: model_fields_set default preservation or adding handwritten migration generation.


def test_adapt_django_04_real_generated_project_check():
    pytest.importorskip("django")
    out = run("run_checks", files=project(), dry_run=False)
    check = next(c for c in out["checks_run"] if c["name"] == "django_check")
    assert check["passed"] is True
    assert "System check identified no issues" in check["evidence"]["stdout"]
# Removal: execute_from_command_line invocation in checks_for.


def test_d1_django_migration_drift_is_refused():
    pytest.importorskip("django")
    files = project()
    files["catalogue/migrations/0001_initial.py"] = (
        "from django.db import migrations, models\nclass Migration(migrations.Migration):\n"
        "    initial = True\n    dependencies = []\n    operations = [migrations.CreateModel("
        "name='Entry', fields=[('id', models.BigAutoField(primary_key=True, serialize=False, auto_created=True, verbose_name='ID')),"
        "('title', models.CharField(max_length=255))])]\n")
    out = run("run_checks", files=files, dry_run=False)
    assert out["completion_state"] == "blocked-escalated"
    assert out["code"] == "G6_E_DJANGO_MIGRATIONS"
    assert next(c for c in out["checks_run"] if c["name"] == "django_check")["passed"] is True
    assert next(c for c in out["checks_run"] if c["name"] == "django_migrations")["passed"] is False
# Removal: makemigrations --check --dry-run check in checks_for.


def test_d1_invalid_model_is_refused_in_real_sandbox():
    pytest.importorskip("django")
    files = project()
    files["catalogue/models.py"] = "from django.db import models\nclass Broken(models.Model):\n    value = models.CharField(max_length=-1)\n"
    out = run("run_checks", files=files, dry_run=False)
    assert out["code"] == "G6_E_DJANGO_CHECK"
    assert out["completion_state"] == "blocked-escalated"
# Removal: django check result refusal in shared checked_reply.


def test_adapt_django_01_settings_never_imported_by_dry_run(tmp_path):
    marker = tmp_path / "host-imported"
    source = f"from pathlib import Path\nPath({str(marker)!r}).write_text('bad')\n"
    out = run("run_checks", files={"manage.py": source, "settings.py": source})
    assert not marker.exists()
    assert out["dry_run"] is True
# Removal: dry-run branch before sandbox execution.


def test_missing_django_is_not_a_pass(monkeypatch):
    from g6.code._types import CHECKER_NOT_INSTALLED
    from g6.core import sandbox
    original = sandbox.run_script

    def missing(source, *args, **kwargs):
        prefix = (
            "import builtins\n_g6_import = builtins.__import__\n"
            "def _missing(name, *args, **kwargs):\n"
            "    if name == 'django' or name.startswith('django.'):\n"
            "        raise ImportError('Django absent for test')\n"
            "    return _g6_import(name, *args, **kwargs)\n"
            "builtins.__import__ = _missing\n"
        )
        return original(prefix + source, *args, **kwargs)

    monkeypatch.setattr(sandbox, "run_script", missing)
    out = run("run_checks", files={"manage.py": "pass"}, dry_run=False)
    assert out["completion_state"] == "blocked-escalated"
    assert out["code"] == CHECKER_NOT_INSTALLED == "G6_E_CODE_CHECKER_NOT_INSTALLED"
    assert len(out["checks_run"]) == 2 and not out["checks_not_run"]
    assert all(c["passed"] is False and c["code"] == CHECKER_NOT_INSTALLED for c in out["checks_run"])
    assert "not installed" in out["warning_card"]["message"]
    assert 'pip install "g6[django] @ git+https://github.com/basicisabc1994/g6solver"' in out["warning_card"]["message"]
# Removal: raise CheckerUnavailable(CHECKER_NOT_INSTALLED, ...) in Django's import failure branch.

def test_sensitive_registry_refuses_checks():
    assert "G6_E_SENSITIVE_COMPONENT_DENIED" in str(get_registry().invoke("adapt_django", "run_checks", {}, block_sensitive=True).failure())
# Removal: run_checks in __g6_sensitive_ops__.

def test_noop_manage_cannot_fake_checks():
    pytest.importorskip("django")
    files = project()
    files["manage.py"] = "pass"
    files["catalogue/models.py"] = "from django.db import models\nclass Broken(models.Model):\n    value = models.CharField(max_length=-2)\n"
    out = run("run_checks", files=files, dry_run=False)
    assert out["completion_state"] == "blocked-escalated"
    assert out["code"] == "G6_E_DJANGO_CHECK"
# Removal: trusted execute_from_command_line dispatch instead of trusting manage.py exit.

def test_settings_module_placeholder_text_stays_literal():
    from g6.code.adapt_django.checks import checks_for
    for check in checks_for(settings_module="ARGV.settings"):
        ast.parse(check.source)
        assert "'ARGV.settings'" in check.source
# Removal: substitute command before inserting settings_module.

def test_project_settings_import_only_in_child(monkeypatch):
    import builtins
    pytest.importorskip("django")
    monkeypatch.delattr(builtins, "_g6_settings_probe", raising=False)
    files = project()
    files["project/settings.py"] += "import builtins\nbuiltins._g6_settings_probe = True\n"
    out = run("run_checks", files=files, dry_run=False)
    assert next(c for c in out["checks_run"] if c["name"] == "django_check")["passed"] is True
    assert not hasattr(builtins, "_g6_settings_probe")
# Removal: sandbox dispatch before Django settings import.


@pytest.mark.parametrize("parts,login_required,expected_apps", [
    (["models", "views", "urls", "settings"], False, ["catalogue"]),
    (["models", "views", "urls", "settings"], True,
     ["django.contrib.auth", "django.contrib.contenttypes", "django.contrib.sessions", "catalogue"]),
    (["models", "views", "urls", "admin", "settings"], False,
     ["django.contrib.admin", "django.contrib.auth", "django.contrib.contenttypes",
      "django.contrib.sessions", "django.contrib.messages", "catalogue"]),
])
def test_selected_generators_have_only_required_contrib_apps(parts, login_required, expected_apps):
    pytest.importorskip("django")
    spec = {**SPEC, "parts": parts, "views": [{**SPEC["views"][0], "login_required": login_required}]}
    files = run("build", spec=spec)["files"]
    settings = files["project/settings.py"].replace("SECRET_KEY = ''", "SECRET_KEY = 'test-only'")
    assignments = {node.targets[0].id: node.value for node in ast.parse(settings).body
                   if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)}
    assert ast.literal_eval(assignments["INSTALLED_APPS"]) == expected_apps
    files["project/settings.py"] = settings
    files["manage.py"] = "pass"
    out = run("run_checks", files=files, dry_run=False)
    check = next(c for c in out["checks_run"] if c["name"] == "django_check")
    assert check["passed"] is True, check
# Removal: conditional auth import or required apps, middleware and DjangoTemplates configuration in generators.


@pytest.mark.parametrize("exit_code", [None, 0, 7])
@pytest.mark.parametrize("stage", ["settings", "apps", "checks"])
def test_project_system_exit_never_passes_django_checks(stage, exit_code):
    pytest.importorskip("django")
    files = project()
    if stage == "settings":
        files["project/settings.py"] = f"raise SystemExit({exit_code!r})\n"
    elif stage == "apps":
        files["catalogue/models.py"] = f"raise SystemExit({exit_code!r})\n"
    else:
        files["project/settings.py"] += (
            "\nfrom django.core.checks import register\n@register()\n"
            f"def interrupted_check(**kwargs):\n    raise SystemExit({exit_code!r})\n"
        )
    out = run("run_checks", files=files, dry_run=False)
    assert out["completion_state"] == "blocked-escalated"
    assert out["code"] == "G6_E_DJANGO_CHECK"
    assert len(out["checks_run"]) == 2
    assert all(c["passed"] is False for c in out["checks_run"])
    assert not any(e.get("passed") is True for e in out["evidence"])
# Removal: the direct execute_from_command_line(sys.argv) call without swallowing SystemExit in checks._SCRIPT.


def test_settings_planted_reports_then_hard_exit_are_not_check_evidence():
    pytest.importorskip("django")
    files = project()
    files["project/settings.py"] = (
        "import json, os\nfrom pathlib import Path\n"
        "for folder in (Path.cwd(), Path.cwd().parent):\n"
        "    (folder / '_g6_check.json').write_text(json.dumps({\n"
        "        'passed': True, 'evidence': {'planted': True}, 'reason': ''\n"
        "}))\n"
        "os._exit(0)\n"
    )
    out = run("run_checks", files=files, dry_run=False)
    assert out["completion_state"] == "blocked-escalated"
    assert out["code"] == "G6_E_DJANGO_CHECK"
    assert all(c["passed"] is False for c in out["checks_run"])
    assert not any(e.get("passed") is True for e in out["evidence"])
    assert not any(c["evidence"].get("planted") for c in out["checks_run"])
# Removal: authenticated report validation before accepting data in _build.run_checks.


def test_django_wrapper_token_source_is_gone_before_settings_import():
    pytest.importorskip("django")
    files = project()
    files["project/settings.py"] += (
        "\nfrom pathlib import Path\n"
        "assert not (Path.cwd().parent / '_g6_script.py').exists()\n"
    )
    out = run("run_checks", files=files, dry_run=False)
    assert next(c for c in out["checks_run"] if c["name"] == "django_check")["passed"] is True
# Removal: pathlib.Path(__file__).unlink() in the shared wrapper prelude.
