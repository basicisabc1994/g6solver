# SPDX-License-Identifier: Apache-2.0
"""LoRA adapters for a declared local model (owner ruling Q8, "add lora as core function"; META_HARNESS_PLAN.md 9.1).

``train(spec, examples)`` trains a small low-rank adapter on (prompt, completion) pairs from the dev split. The base
model's weights never change: the base folder's file manifest is taken before and after, and a changed one refuses the
result. Training runs out of process under ``g6.core.sandbox`` (a time and memory bound; network refused, so the base
model must already be on disk; it is never downloaded). The adapter is a folder the meta-harness stores by digest; a
profile that names it is a candidate like any other: the same held-out gate, at equal compute, then a person.

The training stack (``torch``, ``transformers``, ``peft``) is the optional ``lora`` extra (``pip install g6[lora]``).
Without it nothing here imports it: ``missing()`` names what is absent and ``train`` says so. ``adapter_loader``
runs a base model with an adapter in this process, for a ``Model(adapter_loader=...)``.
"""
from __future__ import annotations

import importlib.util
import json
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from g6.integrity import build_manifest, manifest_sha256

UNAVAILABLE = "G6_E_META_LORA_UNAVAILABLE"
TRAINING_FAILED = "G6_E_META_LORA_FAILED"
BASE_CHANGED = "G6_E_META_PROTECTED"
REQUIRED = ("torch", "transformers", "peft")


@dataclass(frozen=True)
class LoraSpec:
    base_model: str                 # a local folder holding a Hugging Face causal language model and its tokenizer
    rank: int = 8
    alpha: int = 16
    dropout: float = 0.05
    steps: int = 30
    learning_rate: float = 5e-4
    max_length: int = 256
    seed: int = 0
    target_modules: Any = "all-linear"
    timeout_s: float = 1800.0
    mem_mb: int = 4096

    def problems(self) -> list[str]:
        found = []
        if not Path(self.base_model).is_dir():
            found.append("base_model must be a local folder (the trainer never downloads a model)")
        if not (1 <= self.rank <= 256 and self.alpha > 0 and 0 <= self.dropout < 1 and 1 <= self.steps <= 100_000
                and 0 < self.learning_rate < 1 and 8 <= self.max_length <= 8192):
            found.append("rank 1-256, alpha > 0, dropout in [0, 1), steps 1-100000, 0 < learning_rate < 1, "
                         "max_length 8-8192")
        return found


@dataclass
class TrainResult:
    ok: bool
    folder: Path | None = None
    report: dict[str, Any] = field(default_factory=dict)
    code: str = ""
    message: str = ""
    next_action: str = ""


def missing() -> list[str]:
    """The training packages that are not installed."""
    return [name for name in REQUIRED if importlib.util.find_spec(name) is None]


TRAIN_SCRIPT = r'''
import json, random
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer
from peft import LoraConfig, get_peft_model

spec = json.load(open("spec.json", encoding="utf-8"))
rows = [json.loads(line) for line in open("examples.jsonl", encoding="utf-8") if line.strip()]
torch.manual_seed(spec["seed"])
random.seed(spec["seed"])
tok = AutoTokenizer.from_pretrained(spec["base_model"], local_files_only=True)
if tok.pad_token is None:
    tok.pad_token = tok.eos_token
model = AutoModelForCausalLM.from_pretrained(spec["base_model"], local_files_only=True)
config = LoraConfig(r=spec["rank"], lora_alpha=spec["alpha"], lora_dropout=spec["dropout"],
                    target_modules=spec["target_modules"], task_type="CAUSAL_LM")
model = get_peft_model(model, config)
trainable = [p for p in model.parameters() if p.requires_grad]
opt = torch.optim.AdamW(trainable, lr=spec["learning_rate"])
model.train()
losses = []
for step in range(spec["steps"]):
    prompt, completion = rows[step % len(rows)]
    p_ids = tok(prompt, add_special_tokens=False)["input_ids"]
    c_ids = tok(" " + completion + (tok.eos_token or ""), add_special_tokens=False)["input_ids"]
    ids = (p_ids + c_ids)[: spec["max_length"]]
    labels = ([-100] * len(p_ids) + c_ids)[: spec["max_length"]]
    out = model(input_ids=torch.tensor([ids]), labels=torch.tensor([labels]))
    out.loss.backward()
    opt.step()
    opt.zero_grad()
    losses.append(float(out.loss))
model.save_pretrained(".")
print(json.dumps({"steps": spec["steps"], "first_loss": losses[0], "last_loss": losses[-1],
                  "trainable_parameters": sum(p.numel() for p in trainable)}))
'''


def sandbox_trainer(spec: LoraSpec, rows: Sequence[tuple[str, str]], out: Path) -> dict[str, Any]:
    """Train in a bounded child process; the adapter's files are written to ``out``."""
    from g6.core.sandbox import run_script

    files = {"spec.json": json.dumps(asdict(spec)),
             "examples.jsonl": "\n".join(json.dumps([p, c]) for p, c in rows)}
    ran = run_script(TRAIN_SCRIPT, files, timeout_s=spec.timeout_s, mem_mb=spec.mem_mb,
                     max_output_bytes=512 * 1024 * 1024, file_mb=512)
    if ran.status != "ok":
        raise RuntimeError(f"training {ran.status}: {(ran.stderr or '')[-800:]}")
    for name, data in ran.outputs.items():
        if name not in ("spec.json", "examples.jsonl"):
            (out / name).write_bytes(data)
    return json.loads(ran.stdout.strip().splitlines()[-1])


def train(spec: LoraSpec, rows: Sequence[tuple[str, str]], *,
          trainer: Callable[[LoraSpec, Sequence[tuple[str, str]], Path], dict[str, Any]] | None = None) -> TrainResult:
    if trainer is None and missing():
        return TrainResult(False, code=UNAVAILABLE, message=f"LoRA training needs {missing()}",
                           next_action="pip install 'g6[lora]' (torch, transformers, peft), then train again")
    problems = spec.problems()
    if problems or not rows:
        return TrainResult(False, code=TRAINING_FAILED, message="; ".join(problems) or "no training examples")
    base = Path(spec.base_model)
    before = manifest_sha256(build_manifest(base, patterns=("**/*",)))
    out = Path(tempfile.mkdtemp(prefix="g6_adapter_"))
    try:
        report = (trainer or sandbox_trainer)(spec, list(rows), out)
    except Exception as exc:  # noqa: BLE001 - a failed training run is reported, never raised through
        return TrainResult(False, code=TRAINING_FAILED, message=f"{type(exc).__name__}: {exc}")
    after = manifest_sha256(build_manifest(base, patterns=("**/*",)))
    if after != before:
        return TrainResult(False, code=BASE_CHANGED, message="the base model's files changed during training: the "
                           "adapter is refused (the base model must never change)")
    if not any(out.iterdir()):
        return TrainResult(False, code=TRAINING_FAILED, message="training produced no adapter files")
    return TrainResult(True, out, {**report, "base_manifest_sha256": before, "examples": len(rows)})


class AdapterCaller:
    """A local base model with a LoRA adapter, generating greedily in this process (needs the ``lora`` extra)."""

    def __init__(self, base_model: str, adapter_dir: str | Path, *, max_new_tokens: int = 32) -> None:
        self.base_model, self.adapter_dir, self.max_new_tokens = base_model, str(adapter_dir), max_new_tokens
        self._model: Any = None
        self._tok: Any = None

    def _load(self) -> None:
        from peft import PeftModel
        from transformers import AutoModelForCausalLM, AutoTokenizer

        self._tok = AutoTokenizer.from_pretrained(self.base_model, local_files_only=True)
        base = AutoModelForCausalLM.from_pretrained(self.base_model, local_files_only=True)
        self._model = PeftModel.from_pretrained(base, self.adapter_dir).eval()

    def complete(self, system: str, user: str, *, max_tokens: int = 1024) -> str:
        import torch

        if self._model is None:
            self._load()
        ids = self._tok(f"{system}\n\n{user}", return_tensors="pt")["input_ids"]
        with torch.no_grad():
            out = self._model.generate(ids, max_new_tokens=min(self.max_new_tokens, max_tokens), do_sample=False,
                                       pad_token_id=self._tok.pad_token_id or self._tok.eos_token_id)
        return self._tok.decode(out[0][ids.shape[1]:], skip_special_tokens=True).strip()


def adapter_loader(base_model: str, **kw: Any) -> Callable[[Path], AdapterCaller]:
    """For ``Model(caller, adapter_loader=adapter_loader("path/to/base"))``."""
    return lambda adapter_dir: AdapterCaller(base_model, adapter_dir, **kw)
