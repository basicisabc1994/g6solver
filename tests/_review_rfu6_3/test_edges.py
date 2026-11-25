"""Additional real-disk probes of classification while validation is active."""
from pathlib import Path

_retained = Path(__file__).resolve().parents[1] / "_review_rfu6_1" / "test_evidence.py"
_source = _retained.read_text(encoding="utf-8-sig").split("@pytest.mark.parametrize", 1)[0]
_source = _source.replace(".bt_rev_rfu6_1", ".bt_rev_rfu6_3")
exec(compile(_source, str(_retained), "exec"), globals())


@pytest.mark.parametrize("clear", [False, True], ids=["active-fails-closed", "clear-releases"])
def test_reentrant_classifier_then_retry(env, monkeypatch, clear):
    loaded = start(env)
    source = Path(loaded.__file__)
    stat = env.deps._stat_identity
    outcomes = []
    failure = ModuleNotFoundError("absent optional root", name=OPTIONAL)
    sys.modules.pop(OPTIONAL)
    env.bindings.pop(OPTIONAL)
    (env.disk / "optional.py").unlink()

    def probe(path):
        result = stat(path)
        if str(path) == str(source) and not outcomes:
            assert env.deps._active_reuse_evidence
            outcomes.append(None)
            if clear:
                env.deps.optional_import_roots.cache_clear()
            outcomes[0] = env.deps.optional_dependency_missing("abstraction", failure)
        return result

    with monkeypatch.context() as patch:
        patch.setattr(env.deps, "_stat_identity", probe)
        assert env.families.describe("load_state")["available"] is True
    assert outcomes == [clear]
    assert not env.deps._active_reuse_evidence
    assert env.deps.optional_dependency_missing("abstraction", failure) is True
    assert not env.deps._pending_reuse_evidence


@pytest.mark.parametrize("failure_type", [SystemExit, KeyboardInterrupt, BaseException])
@pytest.mark.parametrize("clear", [False, True], ids=["retain", "clear"])
def test_interrupted_validator_then_failed_retry(env, monkeypatch, failure_type, clear):
    loaded = start(env)
    source = Path(loaded.__file__)
    source.rename(env.disk / "retired.py")
    source.write_text(TOOLS, encoding="utf-8")
    saved = env.disk / "saved.py"
    stat = env.deps._stat_identity
    observed = []

    def probe(path):
        result = stat(path)
        if str(path) == str(source) and not observed:
            observed.append(result)
            source.rename(saved)
            # Interrupt the post-stat fingerprint, before full recording starts.
            def interrupt(name):
                if name == "__path__":
                    raise failure_type("interrupted validation")
                raise AttributeError(name)
            loaded.__getattr__ = interrupt
        return result

    with monkeypatch.context() as patch:
        patch.setattr(env.deps, "_stat_identity", probe)
        with pytest.raises(failure_type, match="interrupted validation"):
            env.families.describe("load_state")
    del loaded.__getattr__
    assert observed == [stat(saved)]
    assert env.deps._pending_reuse_evidence
    assert not env.deps._active_reuse_evidence
    if clear:
        env.deps.optional_import_roots.cache_clear()
    assert transfer_and_check(env, saved) == ("optional-install" if clear else "required")
    assert not env.deps._pending_reuse_evidence


@pytest.mark.parametrize("clear", [False, True], ids=["active-fails-closed", "clear-releases"])
def test_startup_hint_during_active_validation(env, monkeypatch, clear):
    from g6 import mcp
    loaded = start(env)
    source = Path(loaded.__file__)
    stat = env.deps._stat_identity
    messages = []

    def probe(path):
        result = stat(path)
        if str(path) == str(source) and not messages:
            assert env.deps._active_reuse_evidence
            messages.append(None)
            sys.modules.pop(REQUIRED)
            env.bindings.pop(REQUIRED)
            if clear:
                env.deps.optional_import_roots.cache_clear()
            failure = ModuleNotFoundError("missing startup dependency", name=REQUIRED)
            messages[0] = mcp._import_error_message(failure)
        return result

    with monkeypatch.context() as patch:
        patch.setattr(env.deps, "_stat_identity", probe)
        assert env.families.describe("load_state")["available"] is True
    assert len(messages) == 1
    assert ("needs the MCP extra" in messages[0]) is clear
    assert not env.deps._pending_reuse_evidence
