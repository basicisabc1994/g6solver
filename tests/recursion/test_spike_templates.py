# SPDX-License-Identifier: Apache-2.0
import hashlib
from types import SimpleNamespace

import pytest

from g6.recursion.spike import templates


def test_trainer_construction_then_every_router_config_off():
    sub = SimpleNamespace(config=SimpleNamespace(output_router_logits=True))
    model = SimpleNamespace(config=SimpleNamespace(output_router_logits=False), modules=lambda: [sub])

    def factory(**kwargs):
        assert kwargs["config"]["router_aux_loss_coef"] == 0.0
        model.config.output_router_logits = True  # measured construction side effect
        return SimpleNamespace(model=model)

    trainer, result = templates.build_trainer(factory, model=model)
    assert trainer.model is model
    assert model.config.output_router_logits is False
    assert sub.config.output_router_logits is False
    assert result["router_aux_loss"] == "off"
    assert result["router_reset_after_trainer"] is True


def test_mask_failure_aborts():
    def bad_mask(_):
        raise ValueError("bad mask")
    with pytest.raises(RuntimeError, match="assistant-only mask failed"):
        templates.apply_mask(object(), bad_mask)


def test_actual_tokens_not_requested_sequence():
    counter = templates.TokenCounter()
    counter.observe([[1, 1, 0], [1, 0, 0]], [[10, 11, -100], [12, -100, -100]])
    counter.observe([[1]], [[-100]])
    assert counter.consumed == 4
    assert counter.supervised == 3
    with pytest.raises(ValueError):
        counter.observe([[1, 0]], [[1, 2]])  # padding must not be supervised


def test_normalise_and_hash_samples():
    samples, sha = templates.normalise_samples([{"text": "a\r\nb\rc"}])
    assert samples == [{"text": "a\nb\nc"}]
    assert len(sha) == 64
    assert templates.normalise_samples(samples) == (samples, sha)


def test_manifest_runs_after_failure_and_from_model_directory(tmp_path):
    model = tmp_path / "model"
    model.mkdir()
    weights = model / "weights.bin"
    weights.write_bytes(b"base")
    listing = f"{hashlib.sha256(b'base').hexdigest()}  weights.bin\n"
    pinned = hashlib.sha256(listing.encode()).hexdigest()
    evidence = {}
    with pytest.raises(RuntimeError, match="step broke"):
        with templates.manifest_guard(model, lambda: listing, pinned, evidence):
            weights.write_bytes(b"changed")
            raise RuntimeError("step broke")
    assert evidence["before"] != evidence["after"]
    with pytest.raises(ValueError, match="manifest digest"):
        with templates.manifest_guard(model, lambda: listing + "\n", pinned, {}):
            pytest.fail("must not enter")


def test_manifest_guard_checks_after_success_without_real_files(monkeypatch):
    # Fake capture stream keeps this test focused on the guard's exit policy.
    import io
    from pathlib import Path

    data = iter([b"base", b"changed"])
    monkeypatch.setattr(Path, "open", lambda *_: io.BytesIO(next(data)))
    listing = f"{hashlib.sha256(b'base').hexdigest()}  weights.bin\n"
    evidence = {}
    with pytest.raises(ValueError, match="base weights differ after step"):
        with templates.manifest_guard(Path.cwd(), lambda: listing, hashlib.sha256(listing.encode()).hexdigest(), evidence):
            pass


def test_manifest_is_read_again_after_step(monkeypatch):
    import io
    from pathlib import Path

    listing = f"{hashlib.sha256(b'base').hexdigest()}  weights.bin\n"
    readings = iter([listing, listing + "\n"])
    monkeypatch.setattr(Path, "open", lambda *_: io.BytesIO(b"base"))
    with pytest.raises(ValueError, match="manifest digest changed after step"):
        with templates.manifest_guard(Path.cwd(), lambda: next(readings),
                                      hashlib.sha256(listing.encode()).hexdigest(), {}):
            pass


@pytest.mark.parametrize("name", ["../weights.bin", "/weights.bin", "C:/weights.bin"])
def test_manifest_refuses_escaping_paths(tmp_path, name):
    listing = "a" * 64 + "  " + name + "\n"
    with pytest.raises(ValueError):
        with templates.manifest_guard(tmp_path, lambda: listing, hashlib.sha256(listing.encode()).hexdigest(), {}):
            pytest.fail("must not enter")


def test_wsl_argv_contains_no_shell():
    argv = templates.wsl_argv("/opt/g6/p0_3_train.py", ["--seq", "2048"])
    assert argv == ("wsl.exe", "--distribution", "Ubuntu-24.04", "--exec", "python3",
                    "/opt/g6/p0_3_train.py", "--seq", "2048")
