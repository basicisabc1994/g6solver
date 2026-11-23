# SPDX-License-Identifier: Apache-2.0
"""Host receipt adapter for G6's existing Boolean satisfiability checker.

The statement is a Boolean formula, and the scoped question is whether it and
the explicit definitions and axioms are satisfiable. No code correspondence is inferred.
"""
from __future__ import annotations

import hashlib
import platform
import sys
import uuid
from pathlib import Path

from g6 import __version__
from g6.core.ledger import digest
from g6.evidence.custody import CHECKER_SCHEMA, FormalRequest, _json_request
from g6.formal.propositional import check_propositional as _check
from g6.workforce import receipt_store


def check_propositional(statement: str, *, definitions: tuple[str, ...], axioms: tuple[str, ...], subject: str,
                        standard: str, predicates: tuple[str, ...], check_id: str) -> tuple[FormalRequest, str]:
    """Run the checker, then store its typed decision and exact inputs in the host run store."""
    if not all(type(parts) is tuple and all(type(p) is str for p in parts)
               for parts in (definitions, axioms, predicates)):
        raise ValueError("definitions, axioms and predicates are tuples of text")
    check_sha256 = hashlib.sha256(Path(_check.__code__.co_filename).read_bytes()).hexdigest()
    environment_sha256 = digest({"python": sys.version, "executable": sys.executable,
        "platform": platform.platform(), "g6_version": __version__, "checker_sha256": check_sha256})
    request = FormalRequest(statement, "g6.formal.propositional.check_propositional", f"g6 {__version__}",
                            definitions, axioms, "g6.propositional/1", subject, standard, predicates, check_id,
                            check_sha256, environment_sha256)
    got = _check(statement, assertions=(*definitions, *axioms))
    result = "unknown"
    artifact = got.artifact
    if (not got.code and artifact is not None and artifact.solver == "propositional"
            and artifact.solver_version == request.version):
        if got.satisfiable is True:
            result = "pass"
        elif got.satisfiable is False:
            result = "fail"
    body = {"schema": CHECKER_SCHEMA, "execution_id": uuid.uuid4().hex,
            "source_before": subject, "source_after": subject, "environment_sha256": environment_sha256,
            "request": _json_request(request), "result": result,
            "checks": {check_id: {"sha256": check_sha256, "result": result}},
            "decision": got.model_dump(mode="json")}
    return request, receipt_store._record(body)
