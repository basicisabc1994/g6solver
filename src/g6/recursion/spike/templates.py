# SPDX-License-Identifier: Apache-2.0
"""Dependency-injected logic distilled from the owner's hand-run probes.

These templates import no training stack and execute no commands. The owner
adapts them into the local driver. The S0 tests supply small plain objects.
"""
from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath


TRAIN_CONFIG = {"rank": 16, "batch_size": 1, "gradient_accumulation_steps": 16,
                "max_steps": 50, "learning_rate": 1e-4, "packing": False,
                "router_aux_loss_coef": 0.0, "seed": 1118,
                "target_modules": ["q_proj", "k_proj", "v_proj", "o_proj"]}


def build_trainer(factory, *, model, **kwargs):
    """Factory adapts the data config to SFTConfig and constructs SFTTrainer.

TRL construction enables gpt-oss router logits: the frozen router's 0.9
auxiliary coefficient adds load-balancing loss. Both controls are required.
"""
    trainer = factory(model=model, config=dict(TRAIN_CONFIG), **kwargs)
    for root in (model, trainer.model):
        root.config.output_router_logits = False
        for module in root.modules():
            if hasattr(module, "config"):
                module.config.output_router_logits = False
    return trainer, {"router_aux_loss": "off", "router_aux_loss_coef": 0.0,
                     "router_reset_after_trainer": True, "router_configs_off": True}


def apply_mask(trainer, mask):
    try:
        return mask(trainer)
    except Exception as error:
        raise RuntimeError(f"assistant-only mask failed: {error}") from error


@dataclass
class TokenCounter:
    """Observe each consumed microbatch only during the measured interval.

    Inputs are CPU lists from the real attention mask and labels, with no
    extrapolation from requested packing, sequence length or accumulation.
    """
    consumed: int = 0
    supervised: int = 0

    def observe(self, attention_mask, labels):
        if len(attention_mask) != len(labels):
            raise ValueError("batch rows differ")
        consumed = supervised = 0
        for mask, targets in zip(attention_mask, labels):
            if len(mask) != len(targets):
                raise ValueError("token and label lengths differ")
            for present, label in zip(mask, targets):
                if type(present) is not int or present not in (0, 1) or type(label) is not int:
                    raise ValueError("integer binary attention mask and integer labels required")
                if not present and label != -100:
                    raise ValueError("padding cannot be supervised")
                consumed += present
                supervised += int(label != -100)
        self.consumed += consumed
        self.supervised += supervised


def normalise_samples(samples):
    normalised = [{**row, "text": row["text"].replace("\r\n", "\n").replace("\r", "\n")} for row in samples]
    payload = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in normalised)
    return normalised, hashlib.sha256(payload.encode("utf-8")).hexdigest()


def wsl_argv(script: str, args=()):
    """An argv template only: no shell expression, quoting or invocation."""
    return ("wsl.exe", "--distribution", "Ubuntu-24.04", "--exec", "python3", script, *args)


@contextmanager
def manifest_guard(model_directory, read_manifest, pinned_sha256, evidence):
    """Capture disk endpoints even if the step raises, using a pinned manifest.

    ``read_manifest`` reads the local manifest text afresh on each call.
    Caller pins a complete weights manifest before the step. This establishes
    endpoint disk hashes only; it says nothing about in-memory base tensors.
    A wrapper must preserve step exceptions and check the resulting evidence.
    """
    manifest_text = read_manifest()
    if hashlib.sha256(manifest_text.encode("utf-8")).hexdigest() != pinned_sha256:
        raise ValueError("manifest digest changed")
    root = Path(model_directory).resolve()
    expected, paths = {}, {}
    for line in manifest_text.splitlines():
        sha, separator, name = line.partition("  ")
        if not separator or len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
            raise ValueError("malformed sha256 manifest")
        posix, windows = PurePosixPath(name), PureWindowsPath(name)
        if not name or posix.is_absolute() or windows.drive or windows.root or ".." in posix.parts or ".." in windows.parts:
            raise ValueError("manifest path must be relative inside model directory")
        path = (root / name).resolve()
        if root not in path.parents or name in expected:
            raise ValueError("manifest path escapes or repeats")
        expected[name], paths[name] = sha, path
    if not expected:
        raise ValueError("empty manifest")

    def capture():
        hashes = {}
        for name, path in paths.items():
            with path.open("rb") as stream:
                hashes[name] = hashlib.file_digest(stream, "sha256").hexdigest()
        return hashes

    def capture_after():
        evidence["sha256_after"] = hashlib.sha256(read_manifest().encode("utf-8")).hexdigest()
        evidence["after"] = capture()

    evidence.update(model_directory=str(root), sha256=pinned_sha256, expected=expected, before=capture())
    if evidence["before"] != expected:
        raise ValueError("base weights differ before step")
    try:
        yield evidence
    except BaseException:
        capture_after()
        raise
    else:
        capture_after()
        if evidence["sha256_after"] != pinned_sha256:
            raise ValueError("manifest digest changed after step")
        if evidence["after"] != expected:
            raise ValueError("base weights differ after step")
