# SPDX-License-Identifier: Apache-2.0
"""What ``g6_find``, ``g6_describe`` and ``g6_components`` may name (the owner's decision 1, 2026-11-16; plan
revision 8, A+E).

G6's built-in catalogue is G6's own text and public, so those three tools declare no source for it
(``g6.core.consent_declarations``). Built-in means shipped by G6: named, with its module, in ``built_in.json`` beside
this file, which :func:`build` writes from the code and a test checks, not merely found under ``g6.__path__``, where
another package's files can also sit. Every other installed component (an entry-point extension, or a package put
into G6's namespace) is the person's: a reply over MCP names one only where the person shared ``EXTENSION_SOURCE``
with the client's provider, and then carries that source, so admission checks the grant again as it sends and records
the disclosure. Outside an MCP call (Python, the command line) nothing is sent and every component is named.

    python -P -m g6.mcp.catalogue --write     # after a built-in component is added, renamed or removed
    python -P -m g6.mcp.catalogue --check     # exit 1 when the file is stale
"""
from __future__ import annotations

import json
import sys
from functools import lru_cache
from pathlib import Path

from g6.core import cloud_consent as policy
from g6.core import registry

#: The source an installed extension's own text stays under.
EXTENSION_SOURCE = "component_catalogue"
#: The components G6 ships, each with its module.
PATH = Path(__file__).resolve().with_name("built_in.json")
FORMAT = "g6.built_in/1"


def build() -> dict[str, str]:
    """Each built-in component the registry finds in this source tree, with its module."""
    return {name: ref.module for name, ref in sorted(registry.get_registry().refs.items()) if ref.source == "builtin"}


def dumps(components: dict[str, str]) -> str:
    """``built_in.json`` as it is written: one component a line, sorted."""
    rows = ",\n".join(f"    {json.dumps(name)}: {json.dumps(module)}" for name, module in sorted(components.items()))
    return f'{{\n  "format": "{FORMAT}",\n  "components": {{\n{rows}\n  }}\n}}\n'


@lru_cache(maxsize=1)
def shipped() -> dict[str, str]:
    """The checked-in list of the components G6 ships (read once)."""
    return dict(json.loads(PATH.read_text(encoding="utf-8"))["components"])


def built_in(name: str) -> bool:
    """Whether ``name`` is installed as G6 shipped it: in the shipped list, from the module the list names."""
    ref = registry.get_registry().refs.get(name)
    return ref is not None and shipped().get(name) == ref.module


def extension(name: str) -> bool:
    """Whether ``name`` is an installed component G6 did not ship (an entry-point extension, or a package put into
    G6's namespace)."""
    return name in registry.get_registry().refs and not built_in(name)


def carry(name: str, source: str = EXTENSION_SOURCE) -> None:
    """The reply now holds the extension's text (or, with ``HOST_SOURCE``, this computer's facts about a component):
    it goes out only where the person shared that source."""
    item = ("extension:" if source == EXTENSION_SOURCE else "host:") + name
    policy.record_sources((policy.Source.text(source, item, name),))


#: The source a description's facts about this computer stay under.
HOST_SOURCE = "host_configuration"
#: What a description over MCP says in their place without ``HOST_SOURCE`` shared.
HOST_FACTS_LEFT_OUT = ("left out: this computer's facts (the key file's path, which extras are installed, and which "
                       "sensitive operations this server allows) are given once the person shares "
                       f"{HOST_SOURCE} with this client (g6_cloud_grant)")


def host_facts(info: dict) -> dict:
    """``g6_describe``'s reply over MCP: G6's catalogue text as it is, and this computer's facts in it (each key's
    ``file``, each extra's ``installed``, ``sensitive_ops_allowed_here``) only where the person shared ``HOST_SOURCE``
    with the client's provider, the reply then carrying that source; otherwise they are left out and
    ``host_facts`` says so. Outside an MCP call nothing is sent and ``info`` is returned whole."""
    capture = policy.current_capture()
    if capture is None or not info.get("available"):
        return info
    if policy.is_granted(HOST_SOURCE, _provider(capture)):
        carry(str(info.get("name", "")), HOST_SOURCE)
        return info
    info = {k: v for k, v in info.items() if k != "sensitive_ops_allowed_here"}
    if isinstance(info.get("keys"), list):
        info["keys"] = [{k: v for k, v in key.items() if k != "file"} for key in info["keys"]]
    if isinstance(info.get("extras"), list):
        info["extras"] = [{k: v for k, v in extra.items() if k != "installed"} for extra in info["extras"]]
    info["host_facts"] = HOST_FACTS_LEFT_OUT
    return info


def _provider(capture: policy.Capture) -> str:
    return capture.client[0] if capture.client else "cloud:unknown"


#: ``g6_describe``'s one reply over MCP, without ``EXTENSION_SOURCE`` shared, for every name G6 did not ship: an
#: installed extension and a name nothing provides read the same, so the reply tells no one which are installed.
UNSHIPPED = {"available": False,
             "note": "No component G6 ships has this name. An installed extension is described only once the person "
                     f"shares {EXTENSION_SOURCE} with this client (g6_cloud_grant)."}


def unshipped_reply(name: str) -> dict | None:
    """``UNSHIPPED`` for ``g6_describe`` of a name G6 did not ship, over MCP without ``EXTENSION_SOURCE`` shared;
    otherwise ``None``, the description may be given (an extension's then carries that source)."""
    capture = policy.current_capture()
    if capture is None or built_in(name):
        return None
    if not policy.is_granted(EXTENSION_SOURCE, _provider(capture)):
        return dict(UNSHIPPED)
    if extension(name):
        carry(name)
    return None


def shown_components() -> list[str]:
    """Every built-in component, and an extension only outside an MCP call or where the person shared
    ``EXTENSION_SOURCE`` with this client's provider (the reply then carries that source)."""
    capture = policy.current_capture()
    shown = []
    for name in registry.get_registry().names():
        if extension(name) and capture is not None:
            if not policy.is_granted(EXTENSION_SOURCE, _provider(capture)):
                continue
            carry(name)
        shown.append(name)
    return shown


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    text = dumps(build())
    if args == ["--write"]:
        PATH.write_text(text, encoding="utf-8", newline="\n")
        return 0
    if args == ["--check"]:
        if PATH.read_text(encoding="utf-8") != text:
            print("built_in.json is stale: run python -P -m g6.mcp.catalogue --write", file=sys.stderr)
            return 1
        return 0
    print("usage: python -P -m g6.mcp.catalogue --write | --check", file=sys.stderr)
    return 2


__all__ = ["EXTENSION_SOURCE", "HOST_FACTS_LEFT_OUT", "HOST_SOURCE", "PATH", "UNSHIPPED", "build", "built_in", "carry",
           "dumps", "extension", "host_facts", "shipped", "shown_components", "unshipped_reply"]

if __name__ == "__main__":
    raise SystemExit(main())
