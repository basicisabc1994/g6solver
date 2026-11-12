# SPDX-License-Identifier: Apache-2.0
"""G6's isolated observation runner. Repository test configuration is never loaded.

The result channel excludes repository test configuration. The bootstrap shares its interpreter with the code
under test: the channel is not an authentication boundary inside Python.
The observation.v2 frame carries canonical value digests and byte sizes with bounded diagnostic prefixes. Existing
observation.v1 frames keep their comparison semantics; neither format authenticates its producer inside the child.
"""
from __future__ import annotations

import ast
import hashlib
import inspect
import json
import os
import stat
import struct
import sys
import time
from pathlib import Path, PurePosixPath

from g6.core import component_process, confine
from g6.core.errors import ISOLATION_UNAVAILABLE
from g6.core.ledger import digest
from g6.workforce import repo_tests as rt
from g6.harness.observation_contract import observable_exception

RUN_TEMPLATES = frozenset({"example.v1", "differential.v1"})
CONFIRMED = "# G6-CONFIRMED-RUNNER\n"
LIMIT = ("Code under test runs in the same process as the bootstrap and can shape its own observed outcome, "
         "as with any test run.")
MAX_FRAME = 1 << 20
MAGIC = b"G6R1\n"
COMPACT = '# G6-COMPACT-REPRODUCTION\n'


class ExecutableSource(str):
    """Compiler custody held in the host, outside the Python text and its JSON representation.

    Free-form admission keeps only the string. Adding arbitrary code also produces an ordinary string; only the
    historical confirmed label can be prepended without losing custody already held by this compiler object.
    """

    def __new__(cls, source: str, observation: dict):
        made = super().__new__(cls, source)
        made._observation = json.dumps(observation, sort_keys=True, ensure_ascii=True, allow_nan=False)
        return made

    @property
    def observation(self) -> dict:
        return json.loads(self._observation)

    def __radd__(self, prefix):
        text = prefix + str(self)
        return ExecutableSource(text, self.observation) if prefix == CONFIRMED else text


def expanded(source: str) -> str:
    """Decode a compact portable reproduction for compiler inspection; it supplies no execution authority."""
    import base64
    import zlib

    if source.startswith(CONFIRMED):
        source = source[len(CONFIRMED):]
    if source.startswith(COMPACT):
        first = ast.parse(source).body[0]
        if not (isinstance(first, ast.Assign) and len(first.targets) == 1
                and isinstance(first.targets[0], ast.Name) and first.targets[0].id == 'CODE_Z85'):
            raise ValueError('missing compact reproduction')
        try:
            source = zlib.decompress(base64.b85decode(ast.literal_eval(first.value))).decode('utf-8')
        except zlib.error as exc:
            raise ValueError('malformed compact reproduction') from exc
    return source


def _fingerprint(text: str) -> dict:
    raw = text.encode('utf-8')
    return {'sha256': hashlib.sha256(raw).hexdigest(), 'size': len(raw), 'prefix': text[:200]}


def _exception_observation(exc, errors, probe):
    """Record the caught type first; unavailable diagnostics can never erase that observation.

    Instance display hooks are not needed for type matching and are deliberately not invoked. Names are diagnostic.
    The type's ancestry, module and qualified name are read through the built-in type's own descriptors, which read
    the class's own slots and namespace: a metaclass's attribute lookup, name properties, display hooks and instance
    or subclass checks are never asked.
    """
    kind = type(exc)
    own = type.__dict__
    unavailable = '<unavailable>'
    one = {'raised': [id(kind)], 'raised_name': unavailable + '.' + unavailable,
           'exception_detail': dict.fromkeys(('str', 'repr', 'arguments', 'metaclass'), unavailable)}
    if probe:
        one['probe'] = {'raised': one['raised_name']}
    try:
        relevant = set(errors.values())
        ancestry = own['__mro__'].__get__(kind)
        one['raised'].extend(id(parent) for parent in ancestry if parent is not kind and id(parent) in relevant)
    except BaseException:
        pass

    def name(field):
        try:
            value = own[field].__get__(kind)
            if type(value) is not str:
                if not any(base is str for base in own['__mro__'].__get__(type(value))):
                    return unavailable
                # The base implementation copies a str subclass without calling its display or arithmetic hooks.
                value = str.__str__(value)
            return value
        except BaseException:
            return unavailable

    try:
        module, qualified = name('__module__'), name('__qualname__')
        full = module + '.' + qualified
        one['raised_name'] = full if len(full) <= 200 else full[:197] + '...'
        if probe:
            shown = qualified if module == 'builtins' else full
            text = json.dumps(shown, sort_keys=True)
            one['probe'] = {'raised': shown if len(text) <= 500 else {
                'sha256': hashlib.sha256(text.encode('utf-8')).hexdigest(), 'start': text[:200]}}
    except BaseException:
        pass
    return one


class Activity(dict):
    """Process-local probe custody; JSON contains exactly the activity's existing fields."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.owned_runs: list[bool] = []
        self.execution_artifacts: list[dict] = []


def write_probes(path: Path, reply: dict, data: bytes) -> None:
    """Publish observation custody separately, after the exact reply bytes have been saved."""
    activities = (reply.get("assessment", {}).get("completion", {}).get("activities", []))
    probes = {one["check"]: digest(one) for one in activities if isinstance(one, Activity)
              and one.owned_runs and all(one.owned_runs) and one.get("template") == "differential.v1"}
    if not probes:
        return
    executions = {one['check']: one.execution_artifacts for one in activities
                  if isinstance(one, Activity) and one.get('check') in probes}
    record = {"version": "review-observations.v1", "reply_sha256": hashlib.sha256(data).hexdigest(),
              "probes": probes, 'executions': executions}
    side = path.with_suffix(".observations.json")
    part = side.with_name(side.name + ".part")
    with part.open("xb") as handle:
        handle.write(json.dumps(record, sort_keys=True).encode("ascii"))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(part, side)


def read_probes(path: Path, data: bytes) -> dict[str, str]:
    """Missing, damaged, foreign or incompletely published custody never admits observations."""
    try:
        side = path.with_suffix(".observations.json")
        if not stat.S_ISREG(os.lstat(side).st_mode):
            return {}
        value = json.loads(side.read_bytes(), object_pairs_hook=_unique)
        if (set(value) != {"version", "reply_sha256", "probes", 'executions'} or value["version"] != "review-observations.v1"
                or value["reply_sha256"] != hashlib.sha256(data).hexdigest() or not isinstance(value["probes"], dict)
                or not all(isinstance(k, str) and isinstance(v, str) for k, v in value["probes"].items())
                or not isinstance(value['executions'], dict) or set(value['executions']) != set(value['probes'])
                or not all(isinstance(runs, list) for runs in value['executions'].values())):
            return {}
        return value["probes"]
    except (component_process.ExecutionInterrupted, component_process.ContainmentFailure):
        raise
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        return {}


def probe_sha256() -> str:
    return hashlib.sha256(BOOTSTRAP.encode("utf-8")).hexdigest()

# Loaded before any repository module. There is no site initialisation, PYTHONPATH, pytest, plugin or ini reader.
BOOTSTRAP = (inspect.getsource(observable_exception) + '\n' + inspect.getsource(_fingerprint) + '\n'
             + inspect.getsource(_exception_observation)) + r'''
import builtins, collections.abc, copy, hashlib, importlib, importlib.machinery, json, os, re, struct, sys
def _name(kind):
    module = getattr(kind, "__module__", None) or ""
    name = getattr(kind, "__qualname__", None) or getattr(kind, "__name__", None) or "?"
    return name if module == "builtins" else module + "." + name

def _shown(value, limit=200):
    try:
        text = repr(value)
    except Exception as exc:
        text = "<a value whose repr raised " + _name(type(exc)) + ">"
    return text if len(text) <= limit else text[:limit - 3] + "..."

def _attributes(value):
    # an object's own attributes, from its __dict__ and the __slots__ of its classes, or None when it has neither
    found, seen = {}, False
    if isinstance(getattr(value, "__dict__", None), dict):
        found.update(vars(value))
        seen = True
    for kind in type(value).__mro__:
        slots = kind.__dict__.get("__slots__", ())
        for slot in [slots] if isinstance(slots, str) else slots:
            if slot not in ("__dict__", "__weakref__") and hasattr(value, slot):
                found[slot] = getattr(value, slot)
                seen = True
    return {str(key): item for key, item in found.items()} if seen else None

def _sorted(items):
    return sorted(items, key=lambda item: json.dumps(item, sort_keys=True))

def _plain(value, depth=0):
    # the value in a canonical structural form, never its repr: a tuple as a list; any mapping as its items, sorted;
    # a set sorted; another object as its type's qualified name and its attributes (__dict__, __slots__), so an
    # insertion order, a hash order or a changed repr is no difference; one with neither by its type and its repr
    if depth > 8:
        return {"deeper": _name(type(value))}
    if value is None or type(value) in (bool, int, str):
        return value
    if type(value) is float:
        return value if value == value and abs(value) != float("inf") else {"float": repr(value)}
    if isinstance(value, type):
        return {"class": _name(value)}
    if isinstance(value, (list, tuple)):
        return [_plain(item, depth + 1) for item in value]
    if isinstance(value, (set, frozenset)):
        return _sorted(_plain(item, depth + 1) for item in value)
    if isinstance(value, collections.abc.Mapping):
        if all(type(key) is str for key in value):
            return {key: _plain(item, depth + 1) for key, item in value.items()}
        return _sorted([_plain(key, depth + 1), _plain(item, depth + 1)] for key, item in value.items())
    found = _attributes(value)
    if found is None:
        return {"type": _name(type(value)), "repr": re.sub(r" at 0x[0-9A-Fa-f]+", "", _shown(value))}
    return {"type": _name(type(value)), "attributes": {key: _plain(item, depth + 1) for key, item in found.items()}}

def _bounded(value):
    text = json.dumps(value, sort_keys=True)
    if len(text) <= 500:
        return value
    return {"sha256": hashlib.sha256(bytes(text, "utf-8")).hexdigest(), "start": text[:200]}
root, channel, raw = sys.argv[1:]
data = json.loads(raw)
sink = open(channel, "ab", buffering=0)
write, dumps, pack = sink.write, json.dumps, struct.pack
is_instance, identity, kind_of = isinstance, id, type
sys.dont_write_bytecode = True
# The channel lives in a fresh G6-owned folder. -B prevents writes; a fresh prefix also prevents cache reads.
sys.pycache_prefix = os.path.join(os.path.dirname(channel), "pycache")
def _no_sourceless(self, fullname):
    raise ImportError("G6_E_OBSERVATION_UNAVAILABLE: sourceless bytecode is outside the execution manifest")
importlib.machinery.SourcelessFileLoader.get_code = _no_sourceless
sys.path[:0] = [root, os.path.join(root, "src")]
def encode(value):
    try:
        return _fingerprint(dumps(value, sort_keys=True, allow_nan=False))
    except (TypeError, ValueError, RecursionError):
        return None
def load(spec):
    kind, module, attribute = spec[:3]
    if kind == "builtin":
        return getattr(builtins, attribute)
    found = importlib.import_module(module)
    if len(spec) != 4:
        raise RuntimeError("entry point has no resolved repository file")
    wanted = os.path.realpath(os.path.join(root, *spec[3].split("/")))
    where = os.path.realpath(getattr(found, "__file__", ""))
    if os.path.normcase(where) != os.path.normcase(wanted):
        raise RuntimeError("entry point resolved outside its copied file")
    return getattr(found, attribute)
# Evict the resolved module and its parents, including any same-named standard-library cache entry. The encoder,
# writer and import machinery above are already held by the bootstrap.
module = data["function"][1]
for name in list(sys.modules):
    if name == module or name.startswith(module + ".") or module.startswith(name + "."):
        del sys.modules[name]
# A built-in outcome type is read now, before any repository code runs. Nothing else an outcome names is imported or
# initialised before the call: the person confirmed a fresh import of the function's module and the call, no more.
fixed = {}
for spec in data["errors"]:
    if spec[0] == "builtin":
        error = getattr(builtins, spec[2])
        if not observable_exception(error, resolved=True):
            raise RuntimeError("expected error is not an exception type")
        fixed[dumps(spec, sort_keys=True)] = error
namespace_of = type(sys).__dict__["__dict__"].__get__
def held(mapping, name):
    # What an exact dict holds under an exact str key, compared by str's own equality: no key's own hooks run.
    if kind_of(mapping) is dict:
        for key, value in dict.items(mapping):
            if kind_of(key) is str and key == name:
                return value
    return None
def named(spec):
    # After a call: the exception type an outcome names, among the modules that call loaded, by qualified name and
    # from the file G6 resolved. Never an import, and never a lookup that a repository module, class or metaclass
    # can intercept. None when no such exception type is loaded: that outcome cannot be compared with a raise.
    try:
        if len(spec) != 4:
            return None
        namespace = namespace_of(held(sys.modules, spec[1]))
        where = held(namespace, "__file__")
        wanted = os.path.realpath(os.path.join(root, *spec[3].split("/")))
        if kind_of(where) is not str or os.path.normcase(os.path.realpath(where)) != os.path.normcase(wanted):
            return None
        found = held(namespace, spec[2])
        return found if observable_exception(found, resolved=True) else None
    except Exception:
        return None
def resolved():
    found = {}
    for spec in data["errors"]:
        key = dumps(spec, sort_keys=True)
        error = fixed[key] if key in fixed else named(spec)
        alive.append(error)
        found[key] = None if error is None else identity(error)
    return found
function = load(data["function"])
errors, outcomes, alive = {}, [], []
for call in data["calls"]:
    args, kwargs = copy.deepcopy(call["args"]), copy.deepcopy(call.get("kwargs", {}))
    try:
        result = function(*args, **kwargs)
    except BaseException as exc:
        alive.append(exc)
        errors = resolved()
        outcomes.append(_exception_observation(exc, errors, data['probe']))
    else:
        errors = resolved()
        one = {"returned": encode(result), "arguments": [encode(arg) for arg in args]}
        if data["probe"]:
            one["probe"] = ({"raised": None} if data["mode"] == "exceptions" else
                            {"returned": _bounded(_plain(result))})
        outcomes.append(one)
payload = dumps({"version": "observation.v2", "outcomes": outcomes, "errors": errors},
                sort_keys=True, allow_nan=False).encode("utf-8")
if len(payload) > 1048576:
    raise RuntimeError("observation too large")
write(b"G6R1\n" + pack(">I", len(payload)) + payload)
sink.close()
'''


def frame(value: dict) -> bytes:
    payload = json.dumps(value, sort_keys=True, allow_nan=False).encode("utf-8")
    return MAGIC + struct.pack(">I", len(payload)) + payload


def _unique(pairs):
    found = {}
    for key, value in pairs:
        if key in found:
            raise ValueError("duplicate key")
        found[key] = value
    return found


def read_frame(raw: bytes) -> dict | None:
    """Total reader: exactly one bounded, recognised frame, with no prefix or trailing bytes."""
    try:
        if not isinstance(raw, bytes) or not raw.startswith(MAGIC) or len(raw) < len(MAGIC) + 4:
            return None
        size = struct.unpack(">I", raw[len(MAGIC):len(MAGIC) + 4])[0]
        if size > MAX_FRAME or len(raw) != len(MAGIC) + 4 + size:
            return None
        value = json.loads(raw[len(MAGIC) + 4:], object_pairs_hook=_unique,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite")))
        if (not isinstance(value, dict) or set(value) != {"version", "outcomes", "errors"}
                or value["version"] not in {"observation.v1", "observation.v2"}
                or not isinstance(value["outcomes"], list)
                or not isinstance(value["errors"], dict)
                # observation.v2: null says the call loaded no exception type of that name (it is never imported).
                or any(type(v) is not int and (v is not None or value["version"] != "observation.v2")
                       for v in value["errors"].values())):
            return None
        for one in value["outcomes"]:
            if not isinstance(one, dict):
                return None
            fields = set(one) - {"probe"}
            if 'exception_detail' in fields:
                detail = one['exception_detail']
                if (value['version'] != 'observation.v2'
                        or fields != {'raised', 'raised_name', 'exception_detail'}
                        or not isinstance(detail, dict) or set(detail) != {'str', 'repr', 'arguments', 'metaclass'}
                        or any(not isinstance(text, str) or len(text) > 200 for text in detail.values())):
                    return None
                fields.remove('exception_detail')
            if fields == {"returned", "arguments"}:
                if not isinstance(one['arguments'], list):
                    return None
            elif fields == {"raised", "raised_name"}:
                if (not isinstance(one["raised"], list) or not one["raised"]
                        or any(type(v) is not int for v in one["raised"])
                        or not isinstance(one["raised_name"], str)):
                    return None
            else:
                return None
        for one in value["outcomes"]:
            for encoded in ([one["returned"], *one["arguments"]] if "returned" in one else []):
                if (value['version'] == 'observation.v1' and encoded is not None and not isinstance(encoded, str)
                        or not _valid_encoding(encoded)):
                    return None
        return value
    except (ValueError, TypeError, RecursionError, OverflowError, struct.error, UnicodeError):
        return None


def _encoded(value):
    return json.dumps(value, sort_keys=True, allow_nan=False)


def _valid_encoding(value) -> bool:
    if value is None:
        return True
    if isinstance(value, str):  # Existing observation.v1 frames retain their meaning, including in-process witnesses.
        return _encoded(json.loads(value)) == value
    return (isinstance(value, dict) and set(value) == {'sha256', 'size', 'prefix'}
            and isinstance(value['sha256'], str) and len(value['sha256']) == 64
            and all(ch in '0123456789abcdef' for ch in value['sha256'])
            and type(value['size']) is int and value['size'] >= 0
            and isinstance(value['prefix'], str) and len(value['prefix']) <= 200
            and len(value['prefix'].encode('utf-8')) <= value['size'])


def _matches_encoding(observed, expected) -> bool:
    text = _encoded(expected)
    if isinstance(observed, dict):
        wanted = _fingerprint(text)
        return observed['sha256'] == wanted['sha256'] and observed['size'] == wanted['size']
    return observed == text


def finish(code: int | None, raw: bytes, data: dict) -> dict:
    """G6 compares the observation here, in its own process; child exit status supplies no support or failure."""
    observed = read_frame(raw) if code == 0 else None
    calls = data.get("cases", data.get("calls", []))
    if observed is None or len(observed["outcomes"]) != len(calls) or not calls:
        return {"result": "error", "detail": "G6_E_OBSERVATION_UNAVAILABLE: no single completed observation"}
    expected_errors = {_encoded(spec) for spec in data.get("errors", [
        case["error"] for case in calls if case.get("expect") == "raises"])}
    if set(observed["errors"]) != expected_errors or any(
            "returned" in one and len(one["arguments"]) != len(call.get("args", []))
            for call, one in zip(calls, observed["outcomes"], strict=True)):
        return {"result": "error", "detail": "G6_E_OBSERVATION_UNAVAILABLE: observation does not match the scenario"}
    if "cases" not in data:
        if any("probe" not in one for one in observed["outcomes"]):
            return {"result": "error", "detail": "G6_E_OBSERVATION_UNAVAILABLE: missing probe observation"}
        return {"result": "passed", "detail": "G6 observed the calls in its isolated runner", "observed": observed,
                "owned_probe": True, "observations": [{"input": call, "output": one["probe"]}
                    for call, one in zip(calls, observed["outcomes"], strict=True)]}
    failures = []
    for index, (case, outcome) in enumerate(zip(calls, observed["outcomes"], strict=True)):
        expected = case["expect"]
        if expected == "raises":
            token = observed["errors"].get(_encoded(case["error"]))
            if "raised" not in outcome:
                matches = False   # the call returned: it raised no exception of any type
            elif type(token) is not int:
                # The call raised, and loaded no exception type of the confirmed name to compare it with.
                return {"result": "error", "detail": "G6_E_OBSERVATION_UNAVAILABLE: unresolved expected error"}
            else:
                matches = token in outcome["raised"]
        elif expected == "returns":
            matches = "returned" in outcome and _matches_encoding(outcome["returned"], case["value"])
        elif expected == "effect":
            arguments = outcome.get("arguments", [])
            arg = case["argument"]
            matches = ("returned" in outcome and 0 <= arg < len(arguments)
                       and _matches_encoding(arguments[arg], case["value"]))
        else:
            return {"result": "error", "detail": "G6_E_OBSERVATION_UNAVAILABLE: unknown comparison"}
        if not matches:
            failures.append(index)
    return {"result": "failed" if failures else "passed", "observed": observed,
            "detail": ("the confirmed outcome did not match the observed call(s): " + str(failures)) if failures
                      else "the confirmed outcome matched what the entry point returned or raised in G6's runner"}


def bindings(source: str) -> tuple[str, dict] | None:
    """Read only G6's own generated Python literal slots; never execute a proposed check to learn its bindings."""
    try:
        fields = {}
        for one in ast.parse(expanded(source)).body:
            if isinstance(one, ast.Assign) and len(one.targets) == 1 and isinstance(one.targets[0], ast.Name):
                if one.targets[0].id in {"TEMPLATE", "BINDINGS"}:
                    if one.targets[0].id in fields:
                        return None
                    fields[one.targets[0].id] = ast.literal_eval(one.value)
            elif (isinstance(one, ast.Assign) and len(one.targets) == 1
                  and ast.unparse(one.targets[0]) == "BINDINGS['option']"):
                fields["option"] = ast.literal_eval(one.value)
        if fields.get("TEMPLATE") in RUN_TEMPLATES and isinstance(fields.get("BINDINGS"), dict):
            data = fields["BINDINGS"]
            if '_program' in data:
                return None  # only Scenario below can construct G6's executable program
            if "scenario" in data:
                from g6.harness.executable_scenario import Scenario

                executable = Scenario(data["scenario"].encode("ascii"))
                data = {**executable.comparison(data.get("option", fields.get("option"))),
                        "_program": executable.program}
            return fields["TEMPLATE"], data
    except (ValueError, TypeError, SyntaxError, RecursionError, KeyError, IndexError):
        pass
    return None


def located(copy: Path, data: dict) -> dict:
    """Resolve every module against the copy, with the runner's fixed top/src path, never caller import roots."""
    data = json.loads(_encoded(data))
    specs = [data["function"], *data.get("errors", []),
             *(case["error"] for case in data.get("cases", []) if "error" in case)]
    for spec in specs:
        if spec[0] == "builtin":
            continue
        stem = spec[1].replace(".", "/")
        choices = [prefix + stem + suffix for prefix in ("", "src/") for suffix in ("/__init__.py", ".py")]
        resolved = next((rel for rel in choices if (copy / rel).is_file()), None)
        if resolved is None or len(spec) == 4 and resolved != spec[3]:
            raise ValueError("G6_E_OBSERVATION_UNAVAILABLE: the copied entry point does not match")
        spec[:] = [*spec[:3], resolved]
    return data


def request(data: dict) -> dict:
    """Only setup and inputs reach the child. The chosen outcome stays in G6's process."""
    calls = data.get("cases", data.get("calls", []))
    return {"function": data["function"], "calls": [{"args": c["args"], "kwargs": c.get("kwargs", {})} for c in calls],
            "errors": data.get("errors", [case["error"] for case in calls if case.get("expect") == "raises"]),
            "probe": "cases" not in data, "mode": data.get("mode", "outputs")}


def _made(tree: Path, copy: Path, files: dict[str, str], deadline: float) -> None:
    """Write the folder the child runs in from the manifest, never by walking the copy: exactly the files ``files``
    lists (``_copy_snapshot``'s record of the copy), each a new regular file holding the bytes read from the copy's
    file of that name and hashed as they are written. Bytes that do not hash to the manifest's value, a record that
    is not a file's hash (a link the copy holds) and a name that cannot be made new below ``tree`` (another spelling
    of one already written) refuse the run. Nothing else the copy or its file system holds reaches the child:
    another stream of a file or a folder, another name for a file, an entry the manifest does not list."""
    tree.mkdir()
    size = 0
    for count, (rel, wanted) in enumerate(sorted(files.items()), 1):
        parts = PurePosixPath(rel).parts
        target = tree.joinpath(*parts)
        if (not isinstance(wanted, str) or len(wanted) != 64 or any(ch not in "0123456789abcdef" for ch in wanted)
                or not parts or ".." in parts or not target.is_relative_to(tree) or os.name == "nt" and ":" in rel):
            raise rt.CopyNotRun("G6_E_EXECUTION_ARTIFACT_UNAVAILABLE: not a file G6 can write from its bytes: " + rel)
        written = hashlib.sha256()
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with copy.joinpath(*parts).open("rb") as source, target.open("xb") as made:
                while block := source.read(1 << 20):
                    size += len(block)
                    rt._copy_caps(count, size, deadline)
                    written.update(block)
                    made.write(block)
        except FileExistsError as exc:
            raise rt.CopyNotRun("G6_E_EXECUTION_ARTIFACT_UNAVAILABLE: two names of the manifest are one name in the "
                                "folder the check runs in: " + rel) from exc
        if written.hexdigest() != wanted:
            raise rt.CopyNotRun("G6_E_EXECUTION_ARTIFACT_UNAVAILABLE: the copy changed while G6 wrote the folder "
                                "the check runs in: " + rel)


def _beyond(tree: Path) -> dict[str, str]:
    """What a path in ``tree`` carries beyond the files' names and bytes ``_copy_snapshot`` reads, as entries no
    manifest holds: a folder that holds nothing (its name is all it carries, and no file's path implies it), a file
    that has another name (a hard link: its bytes can be written through a path outside the folder), and every
    stream beyond a file's unnamed one (NTFS: ``name:stream`` on a file or on a folder, the top folder included).
    Read with the names and bytes after the child ends, so each is a change like any other. A stream listing that
    fails is an entry too (an unread stream is never read as none). No stream is listed where the volume has no
    named streams, or off Windows."""
    found: dict[str, str] = {}
    for folder, dirs, files in os.walk(tree):
        component_process.checkpoint()
        here = Path(folder).relative_to(tree).as_posix()
        if not dirs and not files:
            found[here + "/"] = "empty folder"
        dirs[:] = [name for name in dirs if not rt._linked(Path(folder) / name)]   # a link is the snapshot's entry
        for name in files:
            if os.lstat(os.path.join(folder, name)).st_nlink != 1:
                found[(Path(folder) / name).relative_to(tree).as_posix() + " (another name)"] = "hard link"
    return {**found, **_streams(tree)}


def _streams(tree: Path) -> dict[str, str]:
    """``_beyond``'s streams: every one beyond a file's unnamed stream, on each file and folder of ``tree``."""
    if os.name != "nt":
        return {}
    import ctypes
    from ctypes import wintypes

    class Found(ctypes.Structure):   # WIN32_FIND_STREAM_DATA: the name reads ":name:$DATA", the unnamed one "::$DATA"
        _fields_ = [("size", ctypes.c_longlong), ("name", ctypes.c_wchar * 296)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.FindFirstStreamW.restype = wintypes.HANDLE
    kernel.FindFirstStreamW.argtypes = [wintypes.LPCWSTR, ctypes.c_int, ctypes.POINTER(Found), wintypes.DWORD]
    kernel.FindNextStreamW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Found)]
    kernel.FindClose.argtypes = [wintypes.HANDLE]
    kernel.GetVolumePathNameW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
    kernel.GetVolumeInformationW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD, wintypes.LPDWORD,
                                             wintypes.LPDWORD, wintypes.LPDWORD, wintypes.LPWSTR, wintypes.DWORD]
    top = os.path.abspath(tree)
    volume, flags = ctypes.create_unicode_buffer(len(top) + 2), wintypes.DWORD()
    if (kernel.GetVolumePathNameW(top, volume, len(volume))
            and kernel.GetVolumeInformationW(volume.value, None, 0, None, None, ctypes.byref(flags), None, 0)
            and not flags.value & 0x00040000):   # FILE_NAMED_STREAMS: this volume has none to list
        return {}
    found: dict[str, str] = {}
    end, invalid = 38, wintypes.HANDLE(-1).value   # ERROR_HANDLE_EOF: no (further) stream
    for folder, dirs, files in os.walk(top):
        component_process.checkpoint()
        dirs[:] = [name for name in dirs if not rt._linked(Path(folder) / name)]   # a link is the snapshot's entry
        for path in (folder, *(os.path.join(folder, name) for name in files)):
            rel = Path(path).relative_to(top).as_posix()
            one = Found()
            handle = kernel.FindFirstStreamW(path if path.startswith("\\\\") else "\\\\?\\" + path, 0,
                                             ctypes.byref(one), 0)
            if handle in (None, invalid):
                if ctypes.get_last_error() != end:
                    found[rel + ":"] = f"streams not read (error {ctypes.get_last_error()})"
                continue
            try:
                while True:
                    if one.name != "::$DATA":
                        found[rel + one.name] = "stream"
                    if not kernel.FindNextStreamW(handle, ctypes.byref(one)):
                        if ctypes.get_last_error() != end:
                            found[rel + ":"] = f"streams not read (error {ctypes.get_last_error()})"
                        break
            finally:
                kernel.FindClose(handle)
    return found


def run(copy: Path, data: dict, node: str, *, timeout_s: int, deadline: float) -> dict:
    """One confined interpreter G6 starts, in a folder G6 writes from the copy's manifest (``_made``), and one
    channel G6 creates before starting it. The folder is read again after the child ends: its names and bytes by the
    ordinary executor's comparison, and what a path in it carries beyond them (``_beyond``)."""
    started = time.monotonic()
    before = rt._copy_snapshot(copy, deadline=deadline)
    data = located(copy, data)
    # G6's own Python, never a repository interpreter or repository site packages.
    python = sys.executable
    with confine.run_folder(prefix="g6_observation_", dir=copy.parent, ignore_cleanup_errors=True) as top:
        own = Path(top)
        channel, boot, log, tree = own / "result.bin", own / "bootstrap.py", own / "output.log", own / "tree"
        _made(tree, copy, before, deadline)
        remaining = min(timeout_s, deadline - time.monotonic())
        if remaining <= 0:
            return {"verdict": "not_run", "said": "G6_E_OBSERVATION_UNAVAILABLE: timeout before startup"}
        program = data.get("_program", confine.CHILD_PRELUDE + BOOTSTRAP)
        boot.write_text(program, encoding="utf-8", newline="\n")
        with channel.open("xb"):
            pass
        plan = confine.plan(confine.Confinement(read_write=(str(own),),
                            read_only=confine.interpreter_paths(python), network=False), python)
        isolation = plan.record()
        if confine.refused(plan) or not plan.backend:
            plan.release()
            raise rt.RepoRefused(ISOLATION_UNAVAILABLE, confine.refusal(plan))
        try:
            # A canonical program already holds its input. Repeating it on argv also hits Windows' command limit.
            payload = '{}' if '_program' in data else _encoded(request(data))
            command = [python, "-I", "-S", "-B", "-X", "utf8", str(boot), str(tree), str(channel), payload]
            code = rt._bounded(command, cwd=tree, env=rt.child_env(own, network=False),
                               timeout_s=remaining, log=log, confined=plan)
            with channel.open("rb") as handle:
                raw = handle.read(MAX_FRAME + len(MAGIC) + 5)
        except (component_process.ExecutionInterrupted, component_process.ContainmentFailure):
            raise
        except OSError:
            code, raw = None, b""
        finally:
            cleanup = plan.release()
            if cleanup:
                isolation["cleanup"] = cleanup
        found = finish(code, raw, data)
        after = {**rt._copy_snapshot(tree, deadline=deadline), **_beyond(tree)}
    moved = rt._moved(before, after)
    if moved:
        return {"verdict": rt.CHANGED, "changed": moved, "source_before": digest(before),
                "isolation": isolation, "said": "the execution copy changed; its observation is not counted"}
    return {"verdict": found["result"] if found["result"] in {"passed", "failed"} else "not_run",
            "g6_observation": found, "source_before": digest(before), "isolation": isolation,
            "execution_limit": LIMIT,
            "python": python, "interpreter_source": "g6", "import_path": [".", "src"], "own_config": True,
            "seconds": round(time.monotonic() - started, 3), "said": found["detail"], "output": "",
            "command": "python -I -S -B <G6 bootstrap>",
            "bootstrap_sha256": hashlib.sha256(program.encode("utf-8")).hexdigest()}
