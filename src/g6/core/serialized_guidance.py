# SPDX-License-Identifier: Apache-2.0
"""Resolve producer roles in Pydantic's serialization JSON schema.

The inventory names semantic fields on actual G6 classes. A schema generator
attaches those declarations to their serialized nodes; only the resulting JSON
schema supplies wire paths. No runtime payload participates in that resolution.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from pydantic import BaseModel, TypeAdapter
from pydantic.json_schema import GenerateJsonSchema

_ROLE = "x-g6-guidance-tails"
_MODEL = "x-g6-guidance-model"


class GuidanceContractError(ValueError):
    """The declaration does not determine safe serialized guidance positions."""


@dataclass(frozen=True)
class SerializedGuidance:
    """A producer's explicit contract for an intentionally reshaped model.

    Positions name serialized leaves, never attributes. The producer takes
    responsibility for the complete role declaration, including aliases. An
    empty path names scalar output; ``*`` names container elements.
    """

    model: type
    positions: frozenset[tuple[str, ...]]

    def __post_init__(self):
        if not isinstance(self.model, type) or not isinstance(self.positions, frozenset) or not self.positions:
            raise GuidanceContractError("an explicit serialization contract needs positions")
        for path in self.positions:
            if not isinstance(path, tuple) or len(path) > 64:
                raise GuidanceContractError("invalid serialized position")
            if any(not isinstance(key, str) or not key for key in path):
                raise GuidanceContractError("invalid serialized position")


def explicit_serialization_bindings(contract, prefix=()):
    if isinstance(contract, SerializedGuidance):
        return ((prefix, contract.model),)
    if isinstance(contract, Mapping):
        return tuple(binding for path, child in contract.items()
                     for binding in explicit_serialization_bindings(child, (*prefix, *path)))
    if isinstance(contract, (tuple, list)):
        return tuple(binding for child in contract for binding in explicit_serialization_bindings(child, prefix))
    return ()


def validate_model_serialization(model, positions, explicit, path):
    """Resolve this runtime identity's own declaration, independently of a route.

    Explicit serializer permission remains bound to an exact type and position.
    Otherwise a registered type (including one inside a wrapper) contributes its
    own serialization-schema roles; a different route shape does not erase them.
    """
    for pattern, declared in explicit:
        if model is declared and len(pattern) == len(path) and all(
                expected == "*" or expected == actual for expected, actual in zip(pattern, path, strict=True)):
            return positions
    return serialized_positions(model)


def validate_explicit_positions(value, contract):
    """Refuse missing or non-leaf explicit positions, using only declared paths."""
    def at_path(item, path):
        if not path:
            return (item,)
        key, *tail = path
        if key == "*" and isinstance(item, (Mapping, list, tuple)):
            children = item.values() if isinstance(item, Mapping) else item
            return tuple(leaf for child in children for leaf in at_path(child, tail))
        if isinstance(item, Mapping) and key in item:
            return at_path(item[key], tail)
        raise GuidanceContractError("explicit serialized position is absent")

    if isinstance(contract, SerializedGuidance):
        for path in contract.positions:
            for leaf in at_path(value, path):
                if isinstance(leaf, (Mapping, list, tuple)):
                    raise GuidanceContractError("explicit guidance must name serialized leaves")
    elif isinstance(contract, Mapping):
        for path, child in contract.items():
            if explicit_serialization_bindings(child):
                for item in at_path(value, path):
                    validate_explicit_positions(item, child)
    elif isinstance(contract, (list, tuple)):
        for child in contract:
            validate_explicit_positions(value, child)


def _models(core):
    """Declared class identities in the core schema, without deriving paths."""
    found, seen, todo = set(), set(), [core]
    while todo:
        node = todo.pop()
        if not isinstance(node, (dict, list, tuple)) or id(node) in seen:
            continue
        seen.add(id(node))
        if isinstance(node, dict):
            cls = node.get("cls")
            if node.get("type") == "model" and isinstance(cls, type):
                found.add(cls)
            todo.extend(node.values())
        else:
            todo.extend(node)
    return found


def _roles(cls, inventory):
    return frozenset(path for model, paths in inventory.items() if issubclass(cls, model) for path in paths)


def _tag_fields(core, positions):
    """Attach semantic tags before Pydantic applies exclusions and aliases."""
    if core.get("type") == "model-fields":
        fields = {}
        for name, field in core["fields"].items():
            tails = [list(path[1:]) for path in positions if path[0] == name]
            if tails:
                metadata = dict(field.get("metadata", {}))
                extra = metadata.get("pydantic_js_extra", {})
                if not isinstance(extra, dict):
                    raise GuidanceContractError("custom field schema cannot resolve roles")
                metadata["pydantic_js_extra"] = {**extra, _ROLE: tails}
                field = {**field, "metadata": metadata}
            fields[name] = field
        return {**core, "fields": fields}
    if isinstance(core.get("schema"), dict):
        return {**core, "schema": _tag_fields(core["schema"], positions)}
    if positions:
        raise GuidanceContractError("no serialized fields for G6 declaration")
    return core


def serialized_positions(contract):
    from g6.core.agent_guidance import guidance_model_inventory

    inventory = guidance_model_inventory()
    adapter = TypeAdapter(contract)
    expected = {cls for cls in _models(adapter.core_schema) if _roles(cls, inventory)}
    if not expected:
        return frozenset()
    labels = {cls: str(index) for index, cls in enumerate(expected)}

    class RoleSchema(GenerateJsonSchema):
        def model_schema(self, schema):
            cls = schema["cls"]
            bearing = any(_roles(child, inventory) for child in _models(schema))
            decorators = cls.__pydantic_decorators__
            custom_shape = (decorators.model_serializers or decorators.field_serializers
                            or cls.model_dump is not BaseModel.model_dump
                            or cls.__get_pydantic_json_schema__.__func__ is not
                            BaseModel.__get_pydantic_json_schema__.__func__
                            or cls.model_config.get("json_schema_extra") is not None)
            if bearing and custom_shape:
                # A serializer can relocate or duplicate text despite an annotated
                # return type. Require a complete explicit contract in that case.
                raise GuidanceContractError("custom G6 serialization needs explicit positions")
            positions = _roles(cls, inventory)
            tagged = {**schema, "schema": _tag_fields(schema["schema"], positions)} if positions else schema
            previous = self.by_alias
            # model_dump(mode='json') leaves by_alias unset, so each model's own
            # config selects its keys, including models inside a RootModel.
            self.by_alias = bool(cls.model_config.get("serialize_by_alias", False))
            try:
                result = super().model_schema(tagged)
                if positions:
                    self.resolve_ref_schema(result)[_MODEL] = labels[cls]
                return result
            finally:
                self.by_alias = previous

    document = adapter.json_schema(mode="serialization", schema_generator=RoleSchema, by_alias=False)
    observed = set()

    def walk(node, refs=(), depth=0):
        if depth > 64 or not isinstance(node, dict):
            raise GuidanceContractError("unresolvable serialization schema")
        if "$ref" in node:
            ref = node["$ref"]
            if ref in refs or not ref.startswith("#/"):
                raise GuidanceContractError("recursive or external serialization reference")
            target = document
            for part in ref[2:].split("/"):
                target = target[part.replace("~1", "/").replace("~0", "~")]
            return walk(target, (*refs, ref), depth + 1)
        if _MODEL in node:
            observed.add(node[_MODEL])
        positions = {tuple(tail) for tail in node.get(_ROLE, ())}
        for name, child in node.get("properties", {}).items():
            positions.update((name, *path) for path in walk(child, refs, depth + 1))
        for key in ("items", "additionalProperties"):
            if isinstance(node.get(key), dict):
                positions.update(("*", *path) for path in walk(node[key], refs, depth + 1))
        alternatives = node.get("prefixItems", ())
        if alternatives:
            paths = [walk(child, refs, depth + 1) for child in alternatives]
            if any(path != paths[0] for path in paths[1:]):
                raise GuidanceContractError("tuple positions have conflicting roles")
            positions.update(("*", *path) for path in paths[0])
        for key in ("anyOf", "oneOf"):
            alternatives = [child for child in node.get(key, ()) if child.get("type") != "null"]
            paths = [walk(child, refs, depth + 1) for child in alternatives]
            if paths and any(path != paths[0] for path in paths[1:]):
                raise GuidanceContractError("union positions have conflicting roles")
            if paths:
                positions.update(paths[0])
        for child in node.get("allOf", ()):
            positions.update(walk(child, refs, depth + 1))
        return positions

    positions = walk(document)
    if observed != set(labels.values()):
        raise GuidanceContractError("serialization schema lost a G6 declaration")
    return frozenset(positions)
