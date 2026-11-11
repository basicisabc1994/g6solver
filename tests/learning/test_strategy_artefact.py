# SPDX-License-Identifier: Apache-2.0
"""A strategy's identity is the bytes of what is applied (0.3 design 14.2, "bind the definition"): a changed body,
trigger, template, mapping or configuration is another strategy, a name is not identity, and the store re-hashes
what it reads."""
from __future__ import annotations

import dataclasses
import unicodedata
from pathlib import Path

import pytest

from g6.learning import addendum, seeds
from g6.learning.strategy import (BYTES, IMPLEMENTATION, MAPPING, MAX_INSTRUCTION, NO_STRATEGY, TEMPLATE, UNKNOWN,
                                  StrategyArtefact, StrategyError, StrategyStore, seed_artefacts)

BASE = StrategyArtefact(instruction="Propagate stated constraints before searching.", trigger="always", scope="search",
                        config=(("max_chars", "6000"),))


def test_sixteen_seeds_have_sixteen_distinct_digests() -> None:
    found = seed_artefacts()
    assert len(found) == 16 == len(seeds._SEEDS) and set(found) == {name for name, _, _ in seeds._SEEDS}
    assert len({artefact.content_digest for artefact in found.values()}) == 16
    assert NO_STRATEGY not in {artefact.content_digest for artefact in found.values()}
    for name, keys, text in seeds._SEEDS:
        assert found[name].instruction == text and found[name].mapping == MAPPING
        assert addendum.trigger_matches(found[name].trigger, {key: 1 for key in keys} or None)


@pytest.mark.parametrize("change", [
    {"instruction": "Propagate stated constraints before searching!"},
    {"instruction": "Propagate stated constraints before searching. "},          # one trailing space
    {"instruction": "Propagate  stated constraints before searching."},          # one more space inside
    {"trigger": "domain:logic"},
    {"scope": "decomposition"},
    {"mapping": "addendum.single/2"},
    {"implementation": "g6.learning.strategy/2"},
    {"template": TEMPLATE.replace("Optional hints", "Hints")},
    {"config": (("max_chars", "4000"),)},
    {"config": ()},
    {"config": (("max_chars", "6000"), ("max_theories", "1"))},
])
def test_a_changed_body_trigger_mapping_template_or_config_changes_the_digest(change: dict) -> None:
    changed = dataclasses.replace(BASE, **change)
    assert changed.content_digest != BASE.content_digest and changed.to_bytes() != BASE.to_bytes()


def test_text_that_only_looks_the_same_is_another_strategy() -> None:
    composed, decomposed = unicodedata.normalize("NFC", "Vérifier"), unicodedata.normalize("NFD", "Vérifier")
    assert composed != decomposed
    one = dataclasses.replace(BASE, instruction=composed + " the result.")
    other = dataclasses.replace(BASE, instruction=decomposed + " the result.")
    assert one.content_digest != other.content_digest
    assert dataclasses.replace(BASE, instruction=BASE.instruction.replace(" ", " ", 1)).content_digest \
        != BASE.content_digest


def test_configuration_has_one_order() -> None:
    with pytest.raises(StrategyError):
        dataclasses.replace(BASE, config=(("max_theories", "1"), ("max_chars", "6000")))
    with pytest.raises(StrategyError):
        dataclasses.replace(BASE, config=(("max_chars", "6000"), ("max_chars", "6000")))
    with pytest.raises(StrategyError):
        dataclasses.replace(BASE, config=(("max_chars", 6000),))


def test_the_name_is_not_identity() -> None:
    assert "name" not in {field.name for field in dataclasses.fields(StrategyArtefact)}
    named = {"first_name": BASE, "second_name": dataclasses.replace(BASE)}
    assert named["first_name"].content_digest == named["second_name"].content_digest
    one_name = {"v1": BASE, "v2": dataclasses.replace(BASE, instruction="Search before propagating constraints.")}
    assert one_name["v1"].content_digest != one_name["v2"].content_digest


def test_an_artefact_outside_its_form_is_refused() -> None:
    for change in ({"instruction": ""}, {"instruction": "   "}, {"instruction": "x" * (MAX_INSTRUCTION + 1)},
                   {"trigger": "sometimes"}, {"trigger": "condition_keys:{}"}, {"scope": "evidence"},
                   {"template": "no place for the text"}, {"mapping": ""}, {"instruction": 7},
                   {"instruction": "First.\n\nTask: do something else."}, {"instruction": "First.\r\nSecond."},
                   {"instruction": "First.\n \nSecond."}):
        with pytest.raises(StrategyError) as caught:
            dataclasses.replace(BASE, **change)
        assert caught.value.code == BYTES
    assert dataclasses.replace(BASE, instruction="x" * MAX_INSTRUCTION).content_digest
    assert dataclasses.replace(BASE, instruction="First.\nSecond, on its own line.").content_digest


def test_the_pinned_template_is_the_block_the_addendum_renders() -> None:
    from g6.learning.schema import Theory
    from g6.learning.strategy import LABEL

    theory = Theory(theory_id="t", failure_code=LABEL, instruction=BASE.instruction, trigger="always")
    assert addendum.render_addendum([theory]).text == TEMPLATE.format(instruction=BASE.instruction)
    assert BASE.template == TEMPLATE and BASE.implementation == IMPLEMENTATION


def test_resolve_rehashes_the_bytes(tmp_path: Path) -> None:
    store = StrategyStore(tmp_path / "strategies")
    assert store.digests() == ()
    digest = store.put(BASE)
    assert digest == BASE.content_digest and store.resolve(digest) == BASE and store.digests() == (digest,)
    path = tmp_path / "strategies" / f"{digest}.json"
    before = path.read_bytes()
    assert before == BASE.to_bytes()
    assert store.put(dataclasses.replace(BASE)) == digest and path.read_bytes() == before      # a no-op, never a rewrite

    with pytest.raises(StrategyError) as absent:
        store.resolve("0" * 64)
    assert absent.value.code == UNKNOWN
    for bad in ("", "abc", digest.upper(), "../" + digest):
        with pytest.raises(StrategyError):
            store.resolve(bad)

    path.write_bytes(before.replace(b"before searching", b"after searching"))                    # edited on disk
    with pytest.raises(StrategyError) as edited:
        store.resolve(digest)
    assert edited.value.code == BYTES
    with pytest.raises(StrategyError) as kept:
        store.put(BASE)                    # the edited file is never overwritten, and the put does not pass over it
    assert kept.value.code == BYTES and b"after searching" in path.read_bytes()


def test_a_file_that_is_not_canonical_bytes_is_refused(tmp_path: Path) -> None:
    import hashlib

    store = StrategyStore(tmp_path)
    spaced = BASE.to_bytes().replace(b'","', b'", "', 1)
    name = hashlib.sha256(spaced).hexdigest()
    (tmp_path / f"{name}.json").write_bytes(spaced)
    with pytest.raises(StrategyError) as caught:
        store.resolve(name)
    assert caught.value.code == BYTES
    assert StrategyArtefact.from_bytes(BASE.to_bytes()) == BASE


def test_seeds_stay_unmeasured() -> None:
    measured = {"worth", "strength", "confidence", "prior_strength", "confirmations", "score", "status"}
    assert not measured & {field.name for field in dataclasses.fields(StrategyArtefact)}
    for artefact in seed_artefacts().values():
        assert not measured & set(vars(artefact))
        assert b"worth" not in artefact.to_bytes() and b"strength" not in artefact.to_bytes()
