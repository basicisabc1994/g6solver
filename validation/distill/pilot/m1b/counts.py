"""Count repositories only after each configuration's full case passes its limits."""
from __future__ import annotations

from collections.abc import Iterable

from g6.distill.held_out.case_gate import CaseRecord

from .fit import Environment, FitRecord, _case, _imports, _top_modules

CONFIGURATIONS = ("K_M", "K_0", "K_1", "K_2", "K_3", "K_123")


def staged_cases(record: FitRecord, configuration: str) -> tuple[CaseRecord, ...]:
    """Construct the complete candidate case(s); validation errors are never exported."""
    if configuration not in CONFIGURATIONS:
        raise ValueError("unknown fit configuration")
    helper_staging = configuration in {"K_1", "K_123"}
    per_module = configuration in {"K_M", "K_2", "K_123"}
    if not record.tests or not per_module and len(record.tests) != 1:
        raise ValueError("test module count")
    files = dict(record.program)
    if helper_staging:
        files.update({path: text for path, text in record.helpers.items()
                      if path.rsplit("/", 1)[-1] != "conftest.py" and path not in record.tests})
    return tuple(_case(files, record.target, record.changed_lines, tests, record.known_wrong, unit_id=record.bug_id)
                 for tests in record.tests.values())


def summarize(records: Iterable[FitRecord], *, prepared_environment: Environment | None = None) -> dict:
    repositories = {key: set() for key in CONFIGURATIONS}
    exclusions = {key: [] for key in ("V1", "V2", "V3")}
    invalid = {key: [] for key in CONFIGURATIONS}
    flags = []
    for record in records:
        identity = {"repository": record.repository, "bug_id": record.bug_id}
        for code in exclusions:
            if code in record.failed_rules:
                exclusions[code].append({**identity, "flags": list(record.flags)})
        flags.append({**identity, "flags": list(record.flags)})
        for configuration in CONFIGURATIONS:
            failed = set(record.failed_rules)
            if configuration == "K_M":
                failed -= {"V1", "V2", "V3"}
            if configuration in {"K_1", "K_123"}:
                failed.discard("V2")
            if configuration in {"K_2", "K_123"}:
                failed.discard("V1")
            if configuration in {"K_3", "K_123"}:
                failed.discard("V3")
                failed.discard("M5")
                env = prepared_environment or record.environment
                loaded = env.loaded_packages if prepared_environment is not None else env.pytest_dependencies
                if record.repository_modules & _top_modules(loaded):
                    failed.add("M5")
                if prepared_environment is not None:
                    sources = {**record.program, **record.helpers,
                               **{path: pair[1] for path, pair in record.tests.items()}}
                    reached, _ = _imports(sources, tuple(record.tests),
                                          frozenset(record.helpers) | frozenset(record.tests),
                                          env.resolvable_modules)
                    if "V3" in reached:
                        failed.add("V3")
            # Validate independently even where a rule already excludes the candidate.
            try:
                staged_cases(record, configuration)
            except ValueError:
                invalid[configuration].append({**identity, "reason": "case_limits"})
                continue
            if not failed:
                repositories[configuration].add(record.repository)
    def order(item):
        return item["repository"], item["bug_id"]
    return {"counts": {key: len(value) for key, value in repositories.items()},
            "exclusions": {key: sorted(value, key=order) for key, value in exclusions.items()},
            "configuration_exclusions": {key: sorted(value, key=order) for key, value in invalid.items()},
            "flags": sorted(flags, key=order)}
