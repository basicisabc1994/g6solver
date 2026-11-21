# SPDX-License-Identifier: Apache-2.0
"""Refusal text is a fixed vocabulary, even for config and caller secrets."""
import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from g6.sources.ctx_git import config as cfg, ops, runner


@pytest.mark.parametrize("scope,word", [
    ("local", "repository"), ("worktree", "repository"),
    ("global", "global"), ("system", "system"), ("command", "command"),
])
@pytest.mark.parametrize("key,form", [
    ("url./fixture-private-marker.insteadOf", "url.<subsection>.insteadOf"),
    ("url./fixture-private-marker.pushInsteadOf", "url.<subsection>.pushInsteadOf"),
])
def test_rewrite_reason_contains_only_form_scope_and_fixed_sentence(scope, word, key, form):
    def run(*args, **kwargs):
        return runner.Done(0, f"{scope}\0{key}\nfixture-value-marker\0".encode(), b"")

    with pytest.raises(cfg.GitRefused) as refused:
        ops._unsteered(run, "fixture-remote-marker", [], "fetch")
    exc = refused.value
    assert exc.code == ops.STEERED
    assert exc.reason == (
        f"{word} config sets {form}; ctx_git refuses configured steering for this network operation"
    )
    assert "fixture-" not in str(ops.refuse("fetch", exc, {}))
    assert "fixture-" not in str(exc)


@pytest.mark.parametrize("key,form", [
    ("http.https://fixture-private-marker/a.b.proxy", "http.<url>.<name>"),
    ("http.fixture-private-marker", "http.<name>"),
    ("remote.fixture-private-marker.proxy", "remote.<subsection>.<name>"),
])
def test_repository_steering_reason_never_carries_key_text(key, form):
    def run(*args, **kwargs):
        return runner.Done(0, f"local\0{key}\nfixture-value-marker\0".encode(), b"")

    with pytest.raises(cfg.GitRefused) as refused:
        ops._unsteered(run, "fixture-private-marker", [], "fetch")
    assert refused.value.reason == (
        f"repository config sets {form}; ctx_git refuses configured steering for this network operation"
    )


@pytest.mark.parametrize("validate", [
    lambda: ops.rev("fixture-private-marker\n", "fixture-label-marker"),
    lambda: ops.ref_name("fixture-private-marker..", "fixture-label-marker"),
    lambda: ops.remote_name("fixture-private-marker/"),
    lambda: cfg.parse("invalid", "fixture-private-marker"),
    lambda: cfg.remote_host("fixture-private-marker://host/path"),
])
def test_caller_text_never_reaches_refusal_exception_or_reply(validate):
    with pytest.raises(cfg.GitRefused) as refused:
        validate()
    assert "fixture-" not in str(refused.value)
    assert "fixture-" not in str(ops.refuse("fetch", refused.value, {}))


def test_unknown_refusal_identifier_is_never_echoed():
    exc = cfg.GitRefused(ops.INPUT, "fixture-private-marker")
    assert "fixture-" not in exc.reason
    assert "fixture-" not in str(exc)


@pytest.mark.parametrize("key,form", [
    ("URL.https://fixture-marker/a.b.PushInsteadOf", "url.<subsection>.pushInsteadOf"),
    ("url..insteadof", "url.<subsection>.insteadOf"),
    ("http.https://fixture-marker/a.b.sslCAInfo", "http.<url>.<name>"),
    ("credential.https://fixture-marker/a.b.helper", "credential.<url>.<name>"),
    ("core.fixture-marker.hooksPath", "core.<subsection>.<name>"),
    ("core.hooksPath", "core.<name>"),
    ("includeIf.fixture-marker.path", "includeIf.<subsection>.<name>"),
    ("push.pushOption", "push.<name>"),
    ("fetch.bundleURI", "fetch.<name>"),
    ("fixture-marker.other.key", "<key>"),
])
def test_renderer_never_copies_even_a_variable_name(key, form):
    from g6.sources.ctx_git import _refusals

    assert _refusals.render("steering", key=key, scope="local") == (
        f"repository config sets {form}; ctx_git refuses configured steering for this network operation"
    )
    assert "fixture-marker" not in _refusals.render("steering", key=key, scope="fixture-marker")


@pytest.mark.parametrize("failure", ["exit", "oserror", "timeout", "oversize"])
def test_runner_does_not_copy_argument_or_upstream_exception(monkeypatch, failure):
    from g6.core import component_process

    def run(*args, **kwargs):
        if failure == "oserror":
            raise OSError("fixture-private-marker")
        if failure == "timeout":
            raise component_process.ExecutionInterrupted("fixture-timeout-marker")
        if failure == "oversize":
            return SimpleNamespace(returncode=0, stdout=b"xxx", stderr=b"")
        return SimpleNamespace(returncode=1, stdout=b"", stderr=b"fixture-private-marker")

    monkeypatch.setattr(runner.component_process, "run_contained", run)
    monkeypatch.setattr(runner, "git_program", lambda: "git")
    with pytest.raises(cfg.GitRefused) as refused:
        runner._runner(Path.cwd(), {}, 1, settings=(), output_limit=2)("fixture-argument-marker")
    assert "fixture-" not in str(refused.value)
    assert "fixture-" not in str(ops.refuse("fetch", refused.value, {}))


def test_metadata_labels_and_remote_policy_never_copy_private_text():
    assert "fixture-" not in str(cfg._metadata_refusal(OSError("fixture-error-marker"), "fixture-label-marker"))
    problem = cfg.remote_problem("https://fixture-host-marker.invalid/repo",
                                 cfg.GitConfig((), (), "fixture-source-marker"))
    assert problem and "fixture-" not in problem


@pytest.mark.parametrize("name", [
    "repo", ".git", "objects", "refs", "commondir", "repository config",
    "repository config include", "repository config file", "objects/info/alternates",
])
@pytest.mark.parametrize("failure", ["quoted", "unreadable", "outside"])
def test_metadata_refusal_retains_only_the_fixed_file_label(name, failure):
    if failure == "quoted":
        with pytest.raises(cfg.GitRefused) as refused:
            cfg._metadata_entry('"fixture-private-marker"', Path.cwd(), name,
                                SimpleNamespace(roots=(Path.cwd(),)))
        exc = refused.value
        assert exc.code == cfg.PATH_INVALID
        assert exc.reason == f"{name} holds an entry whose spelling G6 cannot check"
    elif failure == "unreadable":
        exc = cfg._metadata_refusal(OSError("fixture-private-marker"), name)
        assert exc.code == cfg.PATH_INVALID
        assert exc.reason == f"{name} could not be checked or read, so git was not run"
    else:
        from g6.code import _roots
        from g6.code._types import CodeRefused

        exc = cfg._metadata_refusal(CodeRefused(_roots.OUTSIDE_ROOT, "fixture-private-marker"), name)
        assert exc.code == cfg.OUTSIDE_ROOT
        assert exc.reason == f"{name}: the path is outside the checked root"
    assert "fixture-" not in str(exc)
    assert "fixture-" not in str(ops.refuse("status", exc, {}))


@pytest.mark.parametrize("identifier", [
    "metadata_check", "metadata_outside", "metadata_spelling", "metadata_network",
    "metadata_drive_outside", "metadata_drive_relative", "metadata_rooted", "metadata_drive",
])
def test_metadata_selector_rejects_unknown_names_without_echoing_them(identifier):
    from g6.sources.ctx_git import _refusals

    for name in ("fixture-private-marker", "objects/info/alternates/fixture-private-marker", "COMMondir", ""):
        exc = cfg.GitRefused(cfg.PATH_INVALID, identifier, metadata=name)
        assert exc.reason == "ctx_git refused the operation; check the request and the declared policy"
        assert "fixture-" not in str(exc)
        assert _refusals.render(identifier, metadata=name) == exc.reason


@pytest.mark.parametrize("identifier,sentence", [
    ("metadata_network", "objects/info/alternates names a network or device path outside the checked folders"),
    ("metadata_drive_outside", "objects/info/alternates names a drive-relative target outside the checked folders"),
    ("metadata_drive_relative", "objects/info/alternates holds a drive-relative entry whose spelling G6 cannot check"),
    ("metadata_rooted", "objects/info/alternates holds a rooted entry whose target G6 cannot determine"),
    ("metadata_drive", "objects/info/alternates holds a drive entry G6 cannot interpret on this system"),
])
def test_metadata_renderer_retains_the_fixed_file_for_every_entry_sentence(identifier, sentence):
    exc = cfg.GitRefused(cfg.PATH_INVALID, identifier, metadata="objects/info/alternates")
    assert exc.reason == sentence


@pytest.mark.parametrize("declared,sentence", [
    (False, "the remote host is not one of the remote hosts the person declared in remote_hosts"),
    (True, "the remote host failed the public-address policy check"),
])
def test_remote_refusal_retains_fixed_policy_detail_without_host_text(declared, sentence):
    host = "fixture-host-marker.invalid"
    out = ops.Outward(cfg.GitConfig((), (host,) if declared else (), "test"), lambda *a: {},
                      resolver=lambda *a: ["10.0.0.7"])
    with pytest.raises(cfg.GitRefused) as refused:
        ops._reachable([f"https://{host}/repo"], out)
    assert refused.value.code == cfg.REMOTE_REFUSED
    assert refused.value.reason == sentence
    assert "fixture-" not in str(refused.value)


def test_gate_refusal_card_never_copies_gate_text_or_command():
    out = ops.Outward(cfg.GitConfig((), (), "test"), lambda *a: {
        "decision": "block", "reason_codes": ["fixture-code-marker"], "message": "fixture-gate-marker"})
    reply, _ = ops._go("fetch", ["fetch", "fixture-command-marker"], {}, out,
                      ops.Call("fetch", dry_run=False))
    assert reply["code"] == ops.HELD
    assert "fixture-" not in str(reply["warning_card"])
    assert "fixture-" not in reply["reason"]
    assert reply["would_run"] == "git fetch fixture-command-marker"


def test_audit_warning_never_copies_a_problem_argument():
    from g6.sources.ctx_git import block

    reply = ops.refuse("fetch", cfg.GitRefused(ops.INPUT, "fixture-reason-marker"), {})
    reply = block._unrecorded(reply, "fixture-problem-marker")
    assert "fixture-" not in str(reply)


def test_every_refusal_site_uses_the_fixed_renderer_registry():
    folder = Path(cfg.__file__).parent
    registry = folder / "_refusals.py"
    assert registry.is_file(), "every refusal needs the shared fixed-vocabulary renderer"
    from g6.sources.ctx_git import _refusals

    metadata_identifiers = {
        "metadata_check", "metadata_outside", "metadata_spelling", "metadata_network",
        "metadata_drive_outside", "metadata_drive_relative", "metadata_rooted", "metadata_drive",
    }
    selectors = {"steering": {"key", "scope"}, **{name: {"metadata"} for name in metadata_identifiers}}
    sites = []
    for path in folder.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
            if name == "GitRefused":
                sites.append((path.name, node.lineno))
                assert len(node.args) == 2, (path.name, node.lineno)
                reason = node.args[1]
                assert isinstance(reason, ast.Constant) and reason.value in _refusals.MESSAGES, (
                    path.name, node.lineno, "refusal reason must be a registered literal identifier"
                )
                assert {kw.arg for kw in node.keywords} == selectors.get(reason.value, set())
            if name == "render" and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) \
                    and node.func.value.id == "_refusals":
                # Only the exception constructor may render a variable identifier;
                # every direct helper/warning reason uses a registered literal.
                if path.name != "config.py" or not any(
                        isinstance(parent, ast.FunctionDef) and parent.name == "__init__" and node in ast.walk(parent)
                        for parent in ast.walk(tree)):
                    assert isinstance(node.args[0], ast.Constant) and node.args[0].value in _refusals.MESSAGES
                    assert {kw.arg for kw in node.keywords} == selectors.get(node.args[0].value, set())
            if name == "refuse" and isinstance(node.func, ast.Attribute):
                if isinstance(node.func.value, ast.Name) and node.func.value.id == "_reply":
                    assert path.name == "ops.py"
                    assert isinstance(node.args[3], ast.Attribute) and node.args[3].attr == "reason"
                    assert isinstance(node.args[3].value, ast.Name) and node.args[3].value.id == "exc"
        # No alternative exception type may carry a newly interpolated refusal.
        for node in ast.walk(tree):
            if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call):
                assert isinstance(node.exc.func, ast.Name) and node.exc.func.id in {
                    "GitRefused", "_metadata_refusal", "missing_git", "ValueError"}
    assert len(sites) >= 70, "the census must cover config, runner, ops and audit refusals"
    constructor = next(n for n in ast.walk(ast.parse(Path(cfg.__file__).read_text(encoding="utf-8")))
                       if isinstance(n, ast.ClassDef) and n.name == "GitRefused")
    assert any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
               and n.func.attr == "render" for n in ast.walk(constructor))
    assignments = [ast.unparse(n) for n in ast.walk(constructor) if isinstance(n, ast.Assign)]
    assert assignments == ["reason = _refusals.render(identifier, key=key, scope=scope, metadata=metadata)",
                            "self.code, self.reason = (code, reason)"]
    # The exception's displayed message must use that rendered reason too.
    initializers = [n for n in ast.walk(constructor) if isinstance(n, ast.Call)
                    and isinstance(n.func, ast.Attribute) and n.func.attr == "__init__"]
    assert len(initializers) == 1
    assert ast.unparse(initializers[0].args[0]) == "f'[{code}] {reason}'"
    for path in folder.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store) and node.attr == "reason":
                assert path == Path(cfg.__file__) and ast.unparse(node.value) == "self"

    registry_tree = ast.parse(registry.read_text(encoding="utf-8"))
    for node in registry_tree.body:
        if isinstance(node, ast.Assign):
            # Literal tables only: no config-derived text can enter the vocabulary.
            ast.literal_eval(node.value)
    assert all(isinstance(message, str) and ("{" not in message or name in {"steering", *metadata_identifiers})
               for name, message in _refusals.MESSAGES.items())
    assert {name for name in _refusals.MESSAGES if name.startswith("metadata_")} == metadata_identifiers
    assert all(_refusals.MESSAGES[name].startswith("{metadata}") for name in metadata_identifiers)
    assert _refusals.MESSAGES["steering"] == (
        "{scope} config sets {form}; ctx_git refuses configured steering for this network operation"
    )
    # A form selector may choose literals, never return a slice or join of the key.
    selector = next(n for n in registry_tree.body if isinstance(n, ast.FunctionDef) and n.name == "_key_form")
    for node in ast.walk(selector):
        if isinstance(node, ast.Return):
            assert isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)
    assert set(_refusals.SCOPE_WORDS.values()) == {"repository", "global", "system", "command"}
    assert _refusals.METADATA_NAMES == {
        "repo": "repo", ".git": ".git", "objects": "objects", "refs": "refs", "commondir": "commondir",
        "repository config": "repository config", "repository config include": "repository config include",
        "repository config file": "repository config file", "objects/info/alternates": "objects/info/alternates",
    }
    renderer = next(n for n in registry_tree.body if isinstance(n, ast.FunctionDef) and n.name == "render")
    # Only the literal tables and selector can supply rendered text. In particular,
    # returning raw key/scope/metadata or formatting them directly must fail this guard.
    returns = [ast.unparse(n.value) for n in ast.walk(renderer) if isinstance(n, ast.Return)]
    assert set(returns) == {"message", "MESSAGES['refused']", "message.format(scope=word, form=_key_form(key))",
                            "message.format(metadata=label)"}
    assignments = [ast.unparse(n) for n in ast.walk(renderer) if isinstance(n, ast.Assign)]
    assert set(assignments) == {"message = MESSAGES.get(identifier, MESSAGES['refused'])",
                                "word = SCOPE_WORDS.get(scope)", "label = METADATA_NAMES.get(metadata)"}
