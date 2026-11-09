# SPDX-License-Identifier: Apache-2.0
"""An immutable executable scenario, independent of the existing question and answer record formats.

The form displays this record and the digest of its exact interpreter program. The check runs that program with
only the person's option supplied. Missing legacy records require a new dialog before product enforcement.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import PurePosixPath

from g6.core import component_process
from g6.core.ledger import canonical
from g6.harness.observation_contract import observable_exception

SETUP = ("a fresh import of the function's module in a copy of the tree under review, with no other state set up: "
         "the call is made once, with copies of these arguments")
FIELDS = frozenset({"version", "setup", "entrypoint", "input", "outcomes"})
UNAVAILABLE = "G6_E_SCENARIO_UNAVAILABLE"


@dataclass(frozen=True)
class Scenario:
    record: bytes

    def __post_init__(self):
        value = json.loads(self.record)
        if (set(value) != FIELDS or canonical(value).encode("ascii") != self.record
                or value["version"] != "example-scenario.v1" or value["setup"] != SETUP):
            raise ValueError(f"{UNAVAILABLE}: unsupported executable scenario")
        entry = value["entrypoint"]
        if any('error' in one and not observable_exception('.'.join(part for part in one['error'][1:3] if part))
               for one in value['outcomes'].values()):
            raise ValueError(f"{UNAVAILABLE}: a raised outcome must name an observable exception")
        if (not isinstance(entry, list) or len(entry) != 4 or entry[0] != "module"
                or PurePosixPath(entry[3]).is_absolute() or ":" in entry[3]
                or ".." in PurePosixPath(entry[3]).parts):
            raise ValueError(f"{UNAVAILABLE}: unsupported repository entry point")

    @classmethod
    def resolve(cls, root, question):
        from g6.harness import verification_templates as vt
        from g6.workforce import repo_tests

        tree = vt.Tree.read(root, repo_tests.change(root), None, ())
        outcomes = {}
        entry = None
        for label, outcome in question.outcomes.items():
            bound = vt.ExampleBindings.model_validate({"function": question.function,
                "cases": [{"args": question.args, "kwargs": question.kwargs, **outcome}]})
            applied = vt.TEMPLATES["example.v1"].applies(tree, bound, vt.PATCH).data
            entry = applied["function"]
            outcomes[label] = {k: v for k, v in applied["cases"][0].items() if k not in ("args", "kwargs")}
        return cls(canonical({"version": "example-scenario.v1", "setup": SETUP, "entrypoint": entry,
                              "input": {"args": question.args, "kwargs": question.kwargs},
                              "outcomes": outcomes}).encode("ascii"))

    @property
    def presentation(self) -> dict:
        """The executable fields the form displays, read from the same record the child runs."""
        value = json.loads(self.record)
        outcomes = {}
        for label, outcome in value['outcomes'].items():
            if outcome['expect'] == 'returns':
                outcomes[label] = {'returns': outcome['value']}
            elif outcome['expect'] == 'effect':
                outcomes[label] = {'effect': {'argument': outcome['argument'], 'equals': outcome['value']}}
            else:
                error = outcome['error']
                outcomes[label] = {'raises': '.'.join(part for part in error[1:3] if part)}
        return {'setup': value['setup'], 'function': '.'.join(value['entrypoint'][1:3]),
                **value['input'], 'outcomes': outcomes}

    @property
    def program(self) -> str:
        """Exact isolated child bytes, including G6's confinement prelude and the canonical scenario.

        Both offered outcomes belong to the immutable scenario; the selected option never reaches this program.
        The host uses the same record for its comparison after the one observation frame arrives.
        """
        from g6.core import confine
        from g6.harness import behaviour_runner as runner

        scenario = json.loads(self.record)
        data = {"function": scenario["entrypoint"], "calls": [scenario["input"]],
                "errors": [one["error"] for one in scenario["outcomes"].values() if "error" in one],
                "probe": False, "mode": "outputs"}
        return (confine.CHILD_PRELUDE + "\nSCENARIO = " + ascii(self.record.decode("ascii")) + "\n"
                + runner.BOOTSTRAP.replace('data = json.loads(raw)', 'data = ' + ascii(data), 1))

    def comparison(self, option: str) -> dict:
        """The host comparison, compiled once from the canonical record for both execution and reproduction."""
        scenario = json.loads(self.record)
        if option not in scenario['outcomes']:
            raise ValueError(f"{UNAVAILABLE}: an option not in the scenario")
        return {'function': scenario['entrypoint'], 'cases': [
            {**scenario['input'], **scenario['outcomes'][option]}],
            'errors': [one['error'] for one in scenario['outcomes'].values() if 'error' in one]}

    def _wrapper(self, comparison: dict) -> str:
        """A portable reproduction wrapper; G6 itself executes program, without loading this pytest wrapper."""
        import inspect
        from g6.harness import behaviour_runner as runner

        helpers = "\n\n".join(inspect.getsource(one) for one in (
            runner._unique, runner._encoded, runner._fingerprint, runner._valid_encoding,
            runner._matches_encoding, runner.read_frame, runner.finish, runner.request))
        return ("import hashlib, json, struct, subprocess, tempfile, sys, os\n"
                "TEMPLATE = 'example.v1'\nBINDINGS = " + ascii({"scenario": self.record.decode("ascii")})
                + "\nMAGIC = b'G6R1\\n'\nMAX_FRAME = 1048576\nBOOTSTRAP = " + ascii(self.program)
                + "\n" + helpers + '''
def _check_example():
    data = ''' + ascii(comparison) + '''
    with tempfile.TemporaryDirectory() as own:
        channel = os.path.join(own, 'result.bin')
        with open(channel, 'xb'):
            pass
        ran = subprocess.run([sys.executable, '-I', '-S', '-B', '-c', BOOTSTRAP,
                              os.getcwd(), channel, json.dumps(request(data), sort_keys=True)],
                             capture_output=True, timeout=10)
        with open(channel, 'rb') as handle:
            raw = handle.read(MAX_FRAME + len(MAGIC) + 5)
    reading = finish(ran.returncode, raw, data)
    if reading['result'] != 'passed':
        raise AssertionError(reading['detail'])

def test_example():
    import pytest
    try:
        _check_example()
    except AssertionError as exc:
        pytest.fail(str(exc), pytrace=False)
''')

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.program.encode("utf-8")).hexdigest()

    def source(self, option: str) -> str:
        return self._wrapper(self.comparison(option)) + "\nBINDINGS['option'] = " + ascii(option) + "\n"

    def matches(self, question) -> bool:
        """Correspondence without consulting the live repository or resolving an entry point again."""
        value = json.loads(self.record)
        if (value["entrypoint"][1:3] != question.function.rsplit(".", 1)
                or canonical(value["input"]) != canonical({"args": question.args, "kwargs": question.kwargs})
                or set(value["outcomes"]) != set(question.outcomes)):
            return False
        for label, outcome in question.outcomes.items():
            held = value["outcomes"][label]
            if "returns" in outcome:
                expected = {"expect": "returns", "value": outcome["returns"]}
            elif "effect" in outcome:
                expected = {"expect": "effect", "argument": outcome["effect"]["argument"],
                            "value": outcome["effect"]["equals"]}
            else:
                error = held.get("error", [])
                if len(error) < 3 or ".".join(part for part in error[1:3] if part) != outcome["raises"]:
                    return False
                expected = {"expect": "raises", "error": error}
            if canonical(held) != canonical(expected):
                return False
        return True


def save(question, scenario: Scenario) -> None:
    from g6.core.storage import state_path

    if not scenario.matches(question):
        raise ValueError(f"{UNAVAILABLE}: the scenario is not this question")
    path = state_path("scenarios", question.sha256 + ".json")
    record = {"version": "scenario-binding.v1", "question_sha256": question.sha256,
              "scenario_sha256": scenario.sha256, "scenario": scenario.record.decode("ascii")}
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if load(question).record != scenario.record:
            raise ValueError(f"{UNAVAILABLE}: this question already names another executable scenario")
        return
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="ascii",
                   newline="\n") as handle:
        handle.write(canonical(record))
        handle.flush()
        os.fsync(handle.fileno())


def load(question) -> Scenario:
    from g6.core.storage import state_path

    try:
        path = state_path("scenarios", question.sha256 + ".json")
        if path.is_symlink():
            raise ValueError("linked record")
        record = json.loads(path.read_bytes())
        scenario = Scenario(record["scenario"].encode("ascii"))
        expected = {"version": "scenario-binding.v1", "question_sha256": question.sha256,
                    "scenario_sha256": scenario.sha256, "scenario": scenario.record.decode("ascii")}
        if record != expected or not scenario.matches(question):
            raise ValueError("mismatched record")
        return scenario
    except (component_process.ExecutionInterrupted, component_process.ContainmentFailure):
        raise
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise ValueError(f"{UNAVAILABLE}: the executable scenario is unavailable; ask for confirmation again") from exc
