# SPDX-License-Identifier: Apache-2.0
"""What each component needs from outside G6 is declared in its package (FW-05, D9), and the declaration agrees with
the code: ``__g6_outside__`` names the programs the person installs (with the environment variable the component
reads), ``__g6_extras__`` names each extra in pyproject.toml and the imports it provides."""
from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from g6.core.outside import OutsideProgram
from g6.core.registry import get_registry

PYPROJECT = tomllib.loads((Path(__file__).resolve().parents[2] / "pyproject.toml").read_text(encoding="utf-8"))
EXTRAS = PYPROJECT["project"]["optional-dependencies"]

OUTSIDE = {"algebra": {"SageMath"}, "lean": {"Lean"}, "model_checking": {"Java"}, "llm_router": {"Ollama"},
           "prolog": {"SWI-Prolog"}, "workforce": {"git", "pytest"}, "csf": {"git"}, "database": {"PostgreSQL"},
           "adapt_comfyui": {"ComfyUI"}}
OUTSIDE["agent_codex"] = {"Codex"}  # g6.agents
OUTSIDE["agent_claude"] = {"Claude Code"}  # g6.agents
OUTSIDE["adapt_ffmpeg"] = OUTSIDE["adapt_audio"] = {"ffmpeg", "ffprobe"}  # g6.media
OUTSIDE["adapt_voice"] = {"eSpeak NG"}
OUTSIDE["adapt_blender"] = {"Blender"}
OUTSIDE["ctx_cognee"] = {"Cognee"}  # g6.memory
OUTSIDE["ctx_elastic"] = {"Elasticsearch"}  # g6.knowledge
OUTSIDE["ctx_search"] = {"SearXNG"}
OUTSIDE["ctx_git"] = {"git"}  # g6.sources
OUTSIDE["ctx_vision"] = {"tesseract"}
OUTSIDE["swe_diagnostics"] = {"git"}  # g6.code
OUTSIDE["australian_legal_corpus"] = {"Open Australian Legal Corpus"}  # an online dataset
NEEDS = {"abstraction": {"abstraction": ("hypothesis", "numpy")}, "adapt_audio": {"audio": ("numpy",)},
         "adapt_blender": {"image": ("PIL",)}, "adapt_comfyui": {"image": ("PIL",)}, "adapt_synth": {"audio": ("numpy",)},
         "adapt_generative_art": {"procedural": ("PIL", "numpy")}, "adapt_image": {"image": ("PIL",)},
         "agent_langgraph": {"agents-langgraph": ("langgraph",)},
         "agent_autogen": {"agents-autogen": ("autogen_agentchat", "autogen_core")},
         "agent_smolagents": {"agents-smolagents": ("smolagents",)},
         "algebra": {"cas": ("sympy",)},
         "causal": {"causal": ("dowhy",)}, "cegis": {"formal": ("z3",), "optim": ("numpy", "scipy")},
         "csf": {"formal": ("z3",)},
         "database": {"db": ("duckdb", "pg8000")},
         "datalog": {"formal": ("z3",)}, "formal": {"formal": ("z3",)},
         "metaharness": {"lora": ("torch", "transformers", "peft")}, "model_checking": {"formal": ("z3",)},
         "optimisation": {"optim": ("ortools",), "optim-nonlinear": ("scipy",)}}
OUTSIDE["adapt_diagrams"] = {"Graphviz"}  # g6.media
NEEDS["adapt_diagrams"] = {"image": ("PIL",)}
NEEDS["adapt_visualisation"] = {"image": ("PIL",)}
NEEDS["knowledge_converter"] = {"docs": ("pypdf",)}  # g6.knowledge
NEEDS["adapt_sklearn"] = {"ml": ("sklearn", "numpy")}  # g6.ml
NEEDS["adapt_automl"] = {"automl": ("flaml", "sklearn", "numpy")}
NEEDS["ctx_vision"] = {"image": ("PIL",)}  # g6.sources
NEEDS["ctx_colbert"] = {"colbert": ("pylate", "huggingface_hub", "numpy"),
                        "embeddings": ("sentence_transformers", "huggingface_hub", "numpy")}
NEEDS["adapt_pytorch"] = {"torch": ("torch",)}
OUTSIDE["adapt_bayesian"] = {"C compiler for pytensor (g++)"}  # g6.ml
NEEDS["adapt_bayesian"] = {"bayes": ("pymc", "arviz", "pytensor")}
OUTSIDE["adapt_rq"] = {"Redis server", "rq worker"}  # g6.integrations
NEEDS["adapt_rq"] = {"rq": ("rq", "redis")}
# core run 5 merges: removing either package's __g6_extras__ declaration makes its case fail
NEEDS["grounding"] = {"nli": ("sentence_transformers",)}
NEEDS["reasoning"] = {"graph": ("networkx",)}

# g6.code
OUTSIDE["adapt_django"] = set()
OUTSIDE["adapt_library_synthesizer"] = set()
OUTSIDE["adapt_webpage"] = {"Playwright Chromium"}
NEEDS["adapt_django"] = {"django": ("django",)}
NEEDS["adapt_library_synthesizer"] = {"docs": ("pypdf",)}
NEEDS["adapt_webpage"] = {"web": ("tinycss2",), "render": ("playwright",)}


def _module(name: str):  # noqa: ANN202
    return get_registry().get_module(name)


@pytest.mark.parametrize("name", get_registry().names())
def test_declarations_are_well_formed(name: str) -> None:
    mod = _module(name)
    outside = getattr(mod, "__g6_outside__", ())
    assert isinstance(outside, tuple) and all(isinstance(p, OutsideProgram) for p in outside)
    extras = getattr(mod, "__g6_extras__", {})
    assert isinstance(extras, dict)
    for extra, names in extras.items():
        assert extra in EXTRAS, f"{name} declares extra {extra!r}, which pyproject.toml does not define"
        assert isinstance(names, tuple) and names and all(isinstance(n, str) and n for n in names)


@pytest.mark.parametrize("name", get_registry().names())
def test_each_component_declares_what_it_needs(name: str) -> None:
    # RED (legal corpus): remove OUTSIDE["australian_legal_corpus"] above.
    # Removal target (adapt_bayesian): __init__.py __g6_outside__ or __g6_extras__ declaration.
    mod = _module(name)
    assert {p.name for p in getattr(mod, "__g6_outside__", ())} == OUTSIDE.get(name, set())
    assert getattr(mod, "__g6_extras__", {}) == NEEDS.get(name, {})


def test_a_programs_environment_variable_is_the_one_its_component_reads() -> None:
    from g6.algebra.envelope import SAGE_ENV
    from g6.lean.envelope import LEAN_ENV
    from g6.model_checking.envelope import JAVA_ENV
    from g6.prolog.envelope import SWIPL_ENV

    found = {p.name: p for name in ("algebra", "lean", "model_checking", "prolog")
             for p in _module(name).__g6_outside__}
    assert (found["SageMath"].env, found["Lean"].env, found["Java"].env, found["SWI-Prolog"].env) == (
        SAGE_ENV, LEAN_ENV, JAVA_ENV, SWIPL_ENV)
    assert (found["SageMath"].command, found["Lean"].command, found["Java"].command,
            found["SWI-Prolog"].command) == ("sage", "lean", "java", "swipl")
    assert found["Java"].version_args == ("-version",)
    assert found["SWI-Prolog"].licence == "BSD-2-Clause"


def test_ollama_is_the_local_service_the_model_door_defaults_to() -> None:
    from g6.core.llm import DEFAULT_OLLAMA_URL

    (ollama,) = _module("llm_router").__g6_outside__
    assert ollama.url.rstrip("/") == DEFAULT_OLLAMA_URL
