"""Synthetic evaluator custody; every execution boundary uses a double."""
from copy import deepcopy
import importlib
import json
from pathlib import Path

import pytest

from g6.core.ledger import digest
from g6.distill.held_out import oracle
from g6.distill.held_out.case_gate import CaseRecord
from g6.distill.held_out.registration_gate import read_registration
from tests.validation.test_pilot_register import synthetic
from tests.validation.test_pilot_tools import DockerFake, patch


def test_custody_tool_exists():
    assert importlib.util.find_spec("validation.distill.pilot.custody") is not None


def test_registration_has_custody_input():
    import inspect

    register = importlib.import_module("validation.distill.pilot.register")
    assert "custody_metadata" in inspect.signature(register.assemble).parameters


SECRET = "CUSTODY_CONTENT_SENTINEL"


@pytest.fixture(autouse=True)
def no_processes(monkeypatch):
    import subprocess

    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: pytest.fail("process start attempted"))


def tool():
    return importlib.import_module("validation.distill.pilot.custody")


@pytest.fixture
def setup(tmp_path, monkeypatch):
    manifest, cohort = synthetic()
    study = tmp_path / "study"
    study.mkdir()
    path = study / "development.json"
    for bug in cohort["bugs"]:
        bug.update(task_image="g6we-task:synthetic", oracle_image="g6we-oracle:synthetic",
                   task_image_id="sha256:" + "b" * 64, oracle_image_id="sha256:" + "c" * 64)
    path.write_text(json.dumps(cohort), encoding="utf-8")
    home = tmp_path / "home"
    workspace = home / "work"
    workspace.mkdir(parents=True)
    monkeypatch.setenv("G6_HOME", str(home))
    return manifest, cohort, path, home, workspace, tmp_path / "repository"


def material():
    base = {"subject.py": f"def answer(x):\n    return x  # {SECRET}\n",
            "tests/test_answer.py": f"# {SECRET}\ndef test_answer():\n    assert True\n"}
    fixed = {"subject.py": f"def answer(x):\n    return x + 1  # {SECRET}\n",
             "tests/test_answer.py": base["tests/test_answer.py"] + "\n"}
    return {"name_status": "M\tsubject.py\nM\ttests/test_answer.py\n", "base": base, "fixed": fixed,
            "patch": "".join(patch(p, base[p], fixed[p]) for p in base)}


def prepare(setup, monkeypatch, **kwargs):
    module = tool()
    manifest, cohort, path, _, workspace, repository = setup
    monkeypatch.setattr(module, "ROOT", repository)
    return module.prepare(manifest, path, seed=cohort["pilot_registration"]["seed"],
                          policy=oracle.OraclePolicy(min_mutants=1), workspace=workspace,
                          runner=kwargs.pop("runner", DockerFake(material())), **kwargs)


@pytest.mark.parametrize("role", ["acquisition", "reuse"])
def test_role_refusal_before_open(setup, monkeypatch, role):
    manifest = setup[0]
    monkeypatch.setattr(Path, "read_text", lambda *a, **kw: pytest.fail("cohort opened"))
    with pytest.raises(ValueError, match=f"custody_role_{role}"):
        prepare(setup, monkeypatch, bugs=manifest["roles"][role]["bugs"])


def test_allocation_checked_first(setup, monkeypatch):
    setup[0]["label"] = SECRET
    with pytest.raises(ValueError, match="custody_allocation"):
        prepare(setup, monkeypatch)


@pytest.mark.parametrize("name", ["final-hidden.json", "FINAL-hidden/development.json"])
def test_final_path_refused_before_open(setup, monkeypatch, name):
    values = list(setup)
    values[2] = setup[2].parent / name
    monkeypatch.setattr(Path, "read_text", lambda *a, **kw: pytest.fail("cohort opened"))
    with pytest.raises(ValueError, match="custody_final"):
        prepare(values, monkeypatch)


@pytest.mark.parametrize("destination", ["study", "repository", "acquisition"])
def test_destination_outside_custody_refused(setup, monkeypatch, destination):
    outside = {"study": setup[2].parent, "repository": setup[5],
               "acquisition": setup[2].parent / "pilot-cases"}[destination]
    with pytest.raises(ValueError, match="custody_location"):
        prepare(setup, monkeypatch, output=outside)
    assert not (outside / "records").exists()


@pytest.mark.parametrize("location", ["study", "repository"])
def test_home_nested_in_forbidden_folder_refused(setup, monkeypatch, location):
    root = setup[2].parent if location == "study" else setup[5]
    monkeypatch.setenv("G6_HOME", str(root / "home"))
    with pytest.raises(ValueError, match="custody_location"):
        prepare(setup, monkeypatch)


def test_records_receipts_and_no_content_export(setup, monkeypatch, capsys, caplog):
    import logging

    calls = []

    def run(case, implementation, suite, **kwargs):
        calls.append(case.unit_id)
        print(SECRET)
        logging.warning(SECRET)
        return "passed" if implementation == case.files[case.target] else "failed"

    monkeypatch.setattr(oracle, "_run", run)
    result = prepare(setup, monkeypatch)
    assert calls
    assert not result["refusals"]
    assert len(result["cases"]) == 8
    assert result["label_counts"] == {"admission": {"positive": 0, "negative": 5, "excluded": 0},
                                      "monitor": {"positive": 0, "negative": 3, "excluded": 0}}
    sealed = setup[3] / "distill/held_out"
    for ref in result["cases"]:
        case = CaseRecord.model_validate_json((sealed / "cases" / f"{ref['case_id']}.json").read_text())
        assert case.case_id == ref["case_id"]
        receipt_id = result["oracle_receipts"][case.case_id]["receipt_sha256"]
        receipt = json.loads((sealed / "receipts" / f"{receipt_id}.json").read_text())
        assert digest(receipt) == receipt_id
    captured = capsys.readouterr()
    assert SECRET not in captured.out + captured.err + caplog.text + json.dumps(result)
    assert all("label" not in row for row in result["oracle_receipts"].values())


def test_runner_exception_is_sanitized(setup, monkeypatch, capsys, caplog):
    def run(command):
        print(SECRET)
        raise RuntimeError(SECRET)

    result = prepare(setup, monkeypatch, runner=run)
    assert result["refusals"]
    assert not result["cases"]
    captured = capsys.readouterr()
    assert SECRET not in captured.out + captured.err + caplog.text + repr(result)


def test_cli_metadata_and_registration_round_trip(setup, monkeypatch, capsys):
    custody = tool()
    register = importlib.import_module("validation.distill.pilot.register")
    manifest, cohort, path, home, workspace, repository = setup
    policy = oracle.OraclePolicy(min_mutants=1)
    cohort["pilot_registration"]["policy"] = policy.model_dump(mode="json")
    labels = iter(["positive", "negative", "positive", "negative", "positive", "positive", "negative", "positive"])

    def label(case, **kwargs):
        return oracle.OracleResult(case_id=case.case_id, label=next(labels), reason="synthetic",
                                   policy_sha256=digest(policy.model_dump(mode="json")))

    monkeypatch.setattr(oracle, "label", label)
    monkeypatch.setattr(custody, "ROOT", repository)
    monkeypatch.setattr(register, "ROOT", repository)
    result = prepare(setup, monkeypatch)
    held = {c["case_id"] for c in cohort["pilot_registration"]["cases"] if c["split"] != "tuning"}
    cohort["pilot_registration"]["cases"] = [c for c in cohort["pilot_registration"]["cases"] if c["split"] == "tuning"]
    cohort["pilot_registration"]["oracle_receipts"] = {
        k: dict(v, policy_sha256=digest(policy.model_dump(mode="json")))
        for k, v in cohort["pilot_registration"]["oracle_receipts"].items() if k not in held}
    cohort["pilot_registration"]["study"]["acceptance_controls"] = [c["case_id"] for c in result["cases"][:2]]
    path.write_text(json.dumps(cohort), encoding="utf-8")
    metadata = path.parent / "custody-metadata.json"
    metadata.write_text(json.dumps(result), encoding="utf-8")
    manifest_path = path.parent / "allocation.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    output = path.parent / "draft"
    assert register.main(["--manifest", str(manifest_path), "--cohort", str(path),
                          "--custody-metadata", str(metadata), "--output", str(output)]) == 0
    body = json.loads((output / "registration.json").read_text())
    registration = read_registration(output / "registration.json", expected_digest=digest(body))
    assert len(registration.cases) == 14
    assert SECRET not in (output / "registration.json").read_text() + capsys.readouterr().out
    assert home.is_dir() and workspace.is_dir()


def test_metadata_rejects_extra_content(setup, monkeypatch):
    register = importlib.import_module("validation.distill.pilot.register")
    manifest, cohort, *_ = setup
    bad = {"version": 1, "source": SECRET}
    with pytest.raises(ValueError, match="custody_metadata"):
        register.assemble(manifest, deepcopy(cohort), custody_metadata=bad)


@pytest.fixture
def memory_inputs(monkeypatch):
    """Transport doubles exercise decisions without filesystem staging."""
    from types import SimpleNamespace

    module = tool()
    manifest, cohort = synthetic()
    root = Path.cwd() / ".build" / "custody-transport"
    home, workspace, repository = root / "home", root / "work", root / "repository"
    path = root / "study" / "development.json"
    for bug in cohort["bugs"]:
        bug.update(task_image="g6we-task:synthetic", oracle_image="g6we-oracle:synthetic",
                   task_image_id="sha256:" + "b" * 64, oracle_image_id="sha256:" + "c" * 64)
    monkeypatch.setenv("G6_HOME", str(home))
    monkeypatch.setattr(module, "ROOT", repository)
    stored = {path: json.dumps(cohort).encode()}
    original_read, original_stat, original_dir = Path.read_bytes, Path.stat, Path.is_dir
    monkeypatch.setattr(Path, "read_bytes", lambda p: stored[p] if p in stored else original_read(p))
    monkeypatch.setattr(Path, "stat", lambda p, *a, **kw:
                        SimpleNamespace(st_size=len(stored[p]), st_mode=0) if p in stored else original_stat(p, *a, **kw))
    monkeypatch.setattr(Path, "is_dir", lambda p: p == workspace or original_dir(p))

    def write(folder, kind, body):
        assert folder == home / "distill/held_out"
        ident = digest(body)
        stored[folder / kind / f"{ident}.json"] = json.dumps(body).encode()
        return ident

    monkeypatch.setattr(module, "_write", write)
    return (manifest, cohort, path, home, workspace, repository), stored


@pytest.mark.parametrize("role", ["acquisition", "reuse"])
def test_memory_role_refusal(memory_inputs, monkeypatch, role):
    inputs, _ = memory_inputs
    monkeypatch.setattr(Path, "read_bytes", lambda p: pytest.fail("cohort opened"))
    with pytest.raises(ValueError, match=f"custody_role_{role}"):
        prepare(inputs, monkeypatch, bugs=inputs[0]["roles"][role]["bugs"])


def test_memory_partial_selection_cannot_query_single_label(memory_inputs, monkeypatch):
    inputs, _ = memory_inputs
    with pytest.raises(ValueError, match="custody_units"):
        prepare(inputs, monkeypatch, bugs=inputs[0]["roles"]["admission"]["bugs"][:1])


@pytest.mark.parametrize("change,code", [("allocation", "allocation"), ("final", "final"),
                                        ("output", "location"), ("split", "split"), ("cohort", "cohort")])
def test_memory_boundary_refusals(memory_inputs, monkeypatch, change, code):
    inputs, stored = memory_inputs
    inputs = list(inputs)
    kwargs = {}
    if change == "allocation":
        inputs[0]["label"] = SECRET
    elif change == "final":
        inputs[2] = inputs[2].parent / "final-unopened.json"
    elif change == "output":
        kwargs["output"] = inputs[2].parent / "pilot-cases"
    elif change == "split":
        inputs[1]["pilot_registration"]["seed"] = "different"
    else:
        body = json.loads(stored[inputs[2]])
        body["digest"] = "b" * 64
        stored[inputs[2]] = json.dumps(body).encode()
    with pytest.raises(ValueError, match=f"custody_{code}"):
        prepare(inputs, monkeypatch, **kwargs)


def test_memory_oracle_and_content_capture(memory_inputs, monkeypatch, capsys, caplog):
    import logging

    inputs, stored = memory_inputs
    calls = []

    def run(case, implementation, suite, **kwargs):
        calls.append((implementation, suite))
        print(SECRET)
        logging.error(SECRET)
        return "passed" if implementation == case.files[case.target] else "failed"

    monkeypatch.setattr(oracle, "_run", run)
    result = prepare(inputs, monkeypatch)
    assert calls and len(result["cases"]) == 8 and not result["refusals"]
    assert result["label_counts"]["admission"]["negative"] == 5
    assert result["label_counts"]["monitor"]["negative"] == 3
    records = [CaseRecord.model_validate_json(data) for p, data in stored.items() if p.parent.name == "cases"]
    assert len(records) == 8 and all(SECRET in r.files[r.target] for r in records)
    captured = capsys.readouterr()
    assert SECRET not in captured.out + captured.err + caplog.text + repr(result)
    assert all(set(r) == {"receipt_sha256"} for r in result["oracle_receipts"].values())


def test_memory_failure_capture(memory_inputs, monkeypatch, capsys, caplog):
    def run(command):
        print(SECRET)
        raise RuntimeError(SECRET)

    result = prepare(memory_inputs[0], monkeypatch, runner=run)
    captured = capsys.readouterr()
    assert len(result["refusals"]) == 8 and not result["cases"]
    assert SECRET not in captured.out + captured.err + caplog.text + repr(result)


def test_write_rechecks_custody_before_mkdir(monkeypatch):
    module = tool()
    monkeypatch.setattr(Path, "mkdir", lambda *a, **kw: pytest.fail("write outside custody attempted"))
    with pytest.raises(ValueError, match="custody_location"):
        module._write(Path.cwd() / ".build" / "outside", "cases", {})


def test_memory_registration_round_trip(memory_inputs, monkeypatch):
    inputs, stored = memory_inputs
    register = importlib.import_module("validation.distill.pilot.register")
    policy = oracle.OraclePolicy(min_mutants=1)
    labels = iter(["positive", "negative", "positive", "negative", "positive", "positive", "negative", "positive"])

    def label(case, **kwargs):
        return oracle.OracleResult(case_id=case.case_id, label=next(labels), reason="synthetic",
                                   policy_sha256=digest(policy.model_dump(mode="json")))

    monkeypatch.setattr(oracle, "label", label)
    exported = prepare(inputs, monkeypatch)
    cohort = deepcopy(inputs[1])
    metadata = cohort["pilot_registration"]
    metadata["policy"] = policy.model_dump(mode="json")
    metadata["cases"] = [c for c in metadata["cases"] if c["split"] == "tuning"]
    metadata["oracle_receipts"] = {c["case_id"]: dict(metadata["oracle_receipts"][c["case_id"]],
                                                     policy_sha256=digest(metadata["policy"]))
                                   for c in metadata["cases"]}
    metadata["study"]["acceptance_controls"] = [c["case_id"] for c in exported["cases"][:2]]
    registration = register.assemble(inputs[0], cohort, custody_metadata=exported)
    path = inputs[2].parent / "registration.json"
    stored[path] = registration.model_dump_json().encode()
    assert read_registration(path, expected_digest=registration.registration_id) == registration
    assert len(registration.cases) == 14
    assert SECRET not in registration.model_dump_json()
    for change in ("allocation", "counts", "receipt", "extra", "duplicate", "refused"):
        broken = deepcopy(exported)
        if change == "allocation":
            broken["allocation_sha256"] = "f" * 64
        elif change == "counts":
            broken["label_counts"]["monitor"]["negative"] += 1
        elif change == "receipt":
            next(iter(broken["oracle_receipts"].values()))["receipt_sha256"] = "f" * 64
        elif change == "extra":
            broken["source"] = SECRET
        elif change == "duplicate":
            broken["cases"][0] = broken["cases"][1]
        else:
            broken["refusals"] = [{"reason_code": "custody_unit_failed"}]
        with pytest.raises(ValueError, match="custody_metadata_invalid"):
            register.assemble(inputs[0], cohort, custody_metadata=broken)


def test_memory_custody_cli_exports_only_metadata(memory_inputs, monkeypatch, capsys, caplog):
    inputs, stored = memory_inputs
    manifest, cohort, path, home, workspace, _ = inputs
    manifest_path, policy_path = path.parent / "allocation.json", path.parent / "oracle-policy.json"
    stored[manifest_path] = json.dumps(manifest).encode()
    stored[policy_path] = oracle.OraclePolicy(min_mutants=1).model_dump_json().encode()
    monkeypatch.setattr(oracle, "_run", lambda case, implementation, suite, **kw:
                        "passed" if implementation == case.files[case.target] else "failed")
    assert tool().main(["--manifest", str(manifest_path), "--cohort", str(path), "--policy", str(policy_path),
                        "--seed", cohort["pilot_registration"]["seed"], "--workspace", str(workspace),
                        "--output", str(home / "distill/held_out")], runner=DockerFake(material())) == 0
    captured = capsys.readouterr()
    body = json.loads(captured.out)
    assert len(body["cases"]) == 8 and not body["refusals"]
    assert SECRET not in captured.out + captured.err + caplog.text


def test_memory_sealed_workspace_refused(memory_inputs, monkeypatch):
    inputs = list(memory_inputs[0])
    inputs[4] = inputs[3] / "distill/held_out"
    with pytest.raises(ValueError, match="custody_workspace"):
        prepare(inputs, monkeypatch)


def test_memory_link_refused_before_read(memory_inputs, monkeypatch):
    inputs, _ = memory_inputs
    original = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda p: p == inputs[2].parent or original(p))
    monkeypatch.setattr(Path, "read_bytes", lambda p: pytest.fail("linked path opened"))
    with pytest.raises(ValueError, match="custody_link"):
        prepare(inputs, monkeypatch)
