# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""Normalize proposed actions into auditable action capsules."""
from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from hashlib import sha256
import json
import os
from pathlib import Path
import re
from typing import Any

from g6.action_gating.schema import ActionCapsule


_ACTION_PROFILES: dict[str, dict[str, Any]] = {
    # ACTION_GATING-01 (audit 2026-10-23): an overwrite is reversible only when the request names a checkpoint or
    # backup the gate can read (``_checkpoint``); otherwise its reversibility is unknown, never assumed.
    "write_file": {
        "target_backend": "local",
        "reversibility": "unknown",
        "blast_radius": "local",
        "safety_domains": ["data_integrity"],
    },
    "delete_file": {
        "target_backend": "local",
        "irreversible_or_costly_to_reverse": True,
        "reversibility": "irreversible",
        "blast_radius": "local",
        "safety_domains": ["data_integrity"],
    },
    # A local read changes nothing, so there is nothing to reverse. What it reads leaves only through a later
    # action (a send, a post, a call), which is gated on its own. A read of a secret-looking path goes to a person
    # (``secret_looking_path``).
    "read_file": {
        "target_backend": "local",
        "reversibility": "reversible",
        "blast_radius": "local",
    },
    "run_code": {
        "target_backend": "local",
        "real_world_effect": True,
        "reversibility": "unknown",
        "blast_radius": "local",
        "safety_domains": ["security", "infrastructure"],
    },
    "call_api": {
        "target_backend": "external_api",
        "external_side_effect": True,
        "reversibility": "unknown",
        "blast_radius": "external_party",
        "safety_domains": ["privacy", "data_integrity"],
    },
    "send_email": {
        "target_backend": "mail",
        "external_side_effect": True,
        "irreversible_or_costly_to_reverse": True,
        "reversibility": "irreversible",
        "blast_radius": "external_party",
        "safety_domains": ["communications", "reputation", "privacy"],
    },
    # A committed change to a database the person running G6 declared (g6.integrations.database). Its reversibility
    # and reach are what the database op observed and the operator declared (a recoverable database, how far a change
    # reaches); left undeclared they stay unknown and the gate asks for more. A workspace checkpoint never undoes it,
    # which is why csf's rollback rates it irreversible: that rating is about checkpoints, this one about the database.
    "database_write": {
        "target_backend": "database",
        "safety_domains": ["data_integrity"],
    },
    "payment": {
        "target_backend": "payments",
        "external_side_effect": True,
        "irreversible_or_costly_to_reverse": True,
        "reversibility": "partially_reversible",
        "blast_radius": "external_party",
        "safety_domains": ["financial", "legal"],
    },
    "account_deletion": {
        "target_backend": "identity",
        "real_world_effect": True,
        "external_side_effect": True,
        "irreversible_or_costly_to_reverse": True,
        "reversibility": "irreversible",
        "blast_radius": "account",
        "safety_domains": ["identity", "privacy", "data_integrity"],
    },
    "production_infrastructure_change": {
        "target_backend": "infrastructure",
        "real_world_effect": True,
        "reversibility": "partially_reversible",
        "blast_radius": "organization",
        "safety_domains": ["infrastructure", "security", "data_integrity"],
    },
    "permission_escalation": {
        "target_backend": "iam",
        "external_side_effect": True,
        "reversibility": "partially_reversible",
        "blast_radius": "organization",
        "safety_domains": ["security", "identity"],
    },
    "plc_write": {
        "target_backend": "plc",
        "real_world_effect": True,
        "reversibility": "unknown",
        "blast_radius": "unknown",
        "safety_domains": ["physical_world", "infrastructure"],
    },
    "gcode_execute": {
        "target_backend": "machine",
        "real_world_effect": True,
        "reversibility": "unknown",
        "blast_radius": "unknown",
        "safety_domains": ["physical_world", "infrastructure"],
    },
}


def has_profile(action_type: str) -> bool:
    """Whether this component has a descriptor (a profile) of ``action_type``; without one its risk facts are claims."""
    return action_type in _ACTION_PROFILES


# Review B1-4: the read_file profile allowed an undeclared read of ``~/.ssh/id_rsa`` or ``.env``, since nothing looked
# at what it reads. These folders, file names, suffixes and words mark a path as secret-looking: private keys, SSH,
# cloud and GnuPG folders, .env files, credential stores and the operating systems' keychains.
_SECRET_FOLDERS = frozenset({".ssh", ".aws", ".azure", ".gnupg", ".kube", ".docker", ".password-store", "gcloud",
                             "keychains", "keyrings"})
_SECRET_FILES = frozenset({"id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", "id_ecdsa_sk", "id_ed25519_sk", ".netrc",
                           "_netrc", ".pgpass", ".git-credentials", ".npmrc", ".pypirc", ".htpasswd", "master.key"})
_SECRET_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".jks", ".keystore", ".ppk", ".kdbx", ".keychain", ".keychain-db",
                    ".env")
_SECRET_WORDS = frozenset({"secret", "secrets", "credential", "credentials", "token", "tokens", "password", "passwords",
                           "passwd", "apikey", "apikeys", "privatekey"})
_SECRET_PAIRS = frozenset({("api", "key"), ("private", "key"), ("access", "key"), ("secret", "key"),
                           ("microsoft", "protect"), ("etc", "shadow"), ("etc", "gshadow")})


def _secret_looking(path: str) -> bool:
    parts = [p for p in str(path).replace("\\", "/").lower().split("/") if p]
    if not parts:
        return False
    name = parts[-1]
    if _SECRET_FOLDERS.intersection(parts) or name in _SECRET_FILES or name.endswith(_SECRET_SUFFIXES):
        return True
    if name.startswith(".env."):
        return True
    words = [w for p in parts for w in re.findall(r"[a-z0-9]+", p)]  # whole words, so tokenizer.py is no token
    return bool(_SECRET_WORDS.intersection(words)) or any(pair in _SECRET_PAIRS for pair in zip(words, words[1:]))


def secret_looking_path(capsule: ActionCapsule) -> str:
    """The first secret-looking path the action names (its target or a parameter's text), or "" when there is none."""
    params = [capsule.normalized_inputs.get("action_params"), capsule.proposed_inputs.get("action_params"),
              capsule.proposed_inputs.get("args")]
    found = [capsule.target_resource] + [v for p in params if isinstance(p, Mapping) for v in p.values()]
    texts = [t for v in found for t in (v if isinstance(v, (list, tuple)) else [v]) if isinstance(t, str)]
    return next((t for t in texts if _secret_looking(t)), "")


# Least to most risky, as classify_impact ranks them (unknown is never treated as low risk).
_REVERSIBILITY_ORDER = ("reversible", "partially_reversible", "unknown", "irreversible")
_BLAST_RADIUS_ORDER = ("none", "local", "account", "unknown", "organization", "external_party", "public")


def _no_safer_than(declared: Any, floor: Any, order: tuple[str, ...]) -> Any:
    """A caller may declare a profiled action riskier than its profile, never safer (audit 002, AG-008)."""
    if floor is None or declared not in order:
        return declared or floor
    return declared if order.index(declared) >= order.index(floor) else floor


# Credential and permission changes (study V2, finding V2-F1): ``update_password`` had no profile, so
# it was only as risky as its declared reach. A name that handles a credential or a permission puts
# the action in the security and identity domains; like a profile, the name can only raise.
_ACCESS_WORDS = frozenset({
    "password", "passwords", "passwd", "passphrase", "passcode", "credential", "credentials", "secret",
    "secrets", "apikey", "mfa", "2fa", "totp", "permission", "permissions", "privilege", "privileges",
    "role", "roles", "acl", "admin", "sudo", "grant", "revoke", "chmod", "chown", "iam",
})
_ACCESS_PAIRS = frozenset({
    ("api", "key"), ("ssh", "key"), ("access", "key"), ("access", "token"), ("auth", "token"), ("refresh", "token"),
})
_ACCESS_DOMAINS = ("security", "identity")


def _names_access(action_type: str) -> bool:
    """Whole words of the name (snake, kebab or camel case), so ``update_controller`` is not a role."""
    words = re.findall(r"[a-z0-9]+", re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", str(action_type)).lower())
    return bool(_ACCESS_WORDS.intersection(words)) or any(pair in _ACCESS_PAIRS for pair in zip(words, words[1:]))


def _safety_domains(declared: Any, action_type: str, profile: dict[str, Any]) -> list[str]:
    """Declared domains add to the profile's and the name's, never replace them (as AG-008 for reach)."""
    floor = list(profile.get("safety_domains") or []) + (list(_ACCESS_DOMAINS) if _names_access(action_type) else [])
    return list(dict.fromkeys(floor + list(declared or []))) or ["other"]


# The fields a profile floors and the normaliser decides, whatever the caller built or declared.
_FLOOR_FIELDS = (
    "real_world_effect", "external_side_effect", "irreversible_or_costly_to_reverse", "reversibility", "blast_radius",
    "required_authority", "observed_authority", "safety_domains",
)


#: Where a request names the checkpoint or backup a write can be undone from: its top level or its parameters.
_CHECKPOINT_KEYS = ("checkpoint", "backup")


def _checkpoint(request: Mapping[str, Any], target: Any) -> str:
    """The first checkpoint or backup the request names that the gate can read itself: an absolute path that exists,
    is readable and is not the target being written. A name alone is the caller's word, so it counts for nothing."""
    params = [request] + [request.get(k) for k in ("action_params", "args", "proposed_inputs")]
    for place in (p for p in params if isinstance(p, Mapping)):
        for key in _CHECKPOINT_KEYS:
            ref = place.get(key)
            if not isinstance(ref, str) or not ref.strip():
                continue
            try:
                path = Path(ref)
                if (path.is_absolute() and path.exists() and os.access(path, os.R_OK)
                        and os.path.normcase(os.path.abspath(path)) != os.path.normcase(os.path.abspath(str(target)))):
                    return str(path)
            except (OSError, ValueError):
                continue
    return ""


def _profile(action_type: str, request: Mapping[str, Any], target: Any) -> dict[str, Any]:
    """The profile of ``action_type`` for this request: a write with a readable checkpoint is reversible."""
    profile = _ACTION_PROFILES.get(action_type, {})
    if action_type == "write_file" and _checkpoint(request, target):
        return {**profile, "reversibility": "reversible"}
    return profile


def _floors(action_type: str, declared: Mapping[str, Any], profile: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """An action's risk fields: its declaration raised to its profile's floor (audit 002, AG-008).

    One function for both ways in, a dict and a built ``ActionCapsule``, so the same action is floored the same way
    and has the same scope hash. Declared effects, reversibility and blast radius can only raise the profile. Authority
    cannot be observed from a request at all, and authority is required of every action type but ``write_file``.
    ``profile`` is the request's own (``_profile``); by default the action type's.
    """
    profile = _ACTION_PROFILES.get(action_type, {}) if profile is None else profile
    return {
        "real_world_effect": bool(declared.get("real_world_effect") or profile.get("real_world_effect")),
        "external_side_effect": bool(declared.get("external_side_effect") or profile.get("external_side_effect")),
        "irreversible_or_costly_to_reverse": bool(
            declared.get("irreversible_or_costly_to_reverse") or profile.get("irreversible_or_costly_to_reverse")
        ),
        "reversibility": _no_safer_than(
            declared.get("reversibility"), profile.get("reversibility"), _REVERSIBILITY_ORDER
        ) or "unknown",
        "blast_radius": _no_safer_than(
            declared.get("blast_radius"), profile.get("blast_radius"), _BLAST_RADIUS_ORDER
        ) or "unknown",
        "required_authority": list(declared.get("required_authority") or (["owner"] if action_type != "write_file" else [])),
        "observed_authority": [],
        "safety_domains": _safety_domains(declared.get("safety_domains"), action_type, profile),
    }


def _floor_capsule(capsule: ActionCapsule) -> ActionCapsule:
    """A built capsule with the floors a dict gets; everything else as built, so flooring twice changes nothing."""
    floors = _floors(capsule.action_type, {name: getattr(capsule, name) for name in _FLOOR_FIELDS},
                     _profile(capsule.action_type, {**capsule.normalized_inputs, **capsule.proposed_inputs},
                              capsule.target_resource))
    proposed = dict(capsule.proposed_inputs)
    if capsule.observed_authority and "observed_authority" not in proposed:
        proposed["observed_authority"] = list(capsule.observed_authority)  # kept as the caller's claim
    return capsule.model_copy(update={**floors, "proposed_inputs": proposed})


def _stable_action_id(data: dict[str, Any]) -> str:
    blob = json.dumps(data, sort_keys=True, separators=(",", ":"), default=str)
    return "ag-" + sha256(blob.encode("utf-8")).hexdigest()[:16]


def _normalize_preconditions(raw_pcs: Any) -> list[Any]:
    """Accept factual preconditions as bare strings or structured dicts/models (EI-090..094).

    Pydantic coerces the returned dicts into ``FactualPrecondition`` on ActionCapsule
    construction; already-typed models pass through unchanged.
    """
    items: list[Any] = []
    for entry in raw_pcs or []:
        if isinstance(entry, str):
            text = entry.strip()
            if text:
                items.append({"statement": text})
        elif isinstance(entry, dict) and (entry.get("statement") or "").strip():
            items.append(entry)
        elif hasattr(entry, "statement") and str(getattr(entry, "statement", "")).strip():
            items.append(entry)
    return items


def _normalize_verification_contract(raw_vc: Any) -> Any:
    """Accept a verification contract as a dict or model, else None (G6-INV-05).

    Pydantic coerces a returned dict into ``VerificationContractModel`` on ActionCapsule
    construction; an already-typed model passes through unchanged. Absent/empty -> None
    (opt-in: the ballistic gate stays silent unless a contract is declared).
    """
    if raw_vc is None:
        return None
    if isinstance(raw_vc, dict):
        return raw_vc if raw_vc else None
    if hasattr(raw_vc, "model_dump") or hasattr(raw_vc, "expected_result"):
        return raw_vc
    return None


def _normalize_optional_model(raw: Any) -> Any:
    """Pass an optional opt-in sub-model (coordination context / world state) through to Pydantic.

    A dict is coerced on ActionCapsule construction; an already-typed model passes through; an
    absent/empty value -> None (opt-in: the coordination and weak-signal gates stay silent unless
    the context is declared, mirroring the verification-contract gate).
    """
    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw if raw else None
    if hasattr(raw, "model_dump"):
        return raw
    return None


def normalize_action_capsule(action: Any) -> ActionCapsule:
    # A built capsule gets only the floors: re-normalising its dump would read `proposed_inputs`
    # back in as parameters and change the action's scope hash. A capsule this function made is
    # already floored, so this is idempotent.
    if isinstance(action, ActionCapsule):
        return _floor_capsule(action)
    if hasattr(action, "model_dump"):
        raw = action.model_dump(mode="json")
    elif isinstance(action, dict):
        raw = dict(action)
    else:
        raw = {"action_type": str(action)}

    metadata = dict(raw.get("metadata") or {})
    params = dict(raw.get("action_params") or raw.get("args") or raw.get("proposed_inputs") or {})
    action_type = raw.get("action_type") or raw.get("op_name") or raw.get("op") or "unknown"
    profile = dict(_ACTION_PROFILES.get(action_type, {}))

    target_backend = raw.get("target_backend") or profile.get("target_backend") or metadata.get("target_backend") or "unknown"
    target_resource = raw.get("target_resource") or params.get("target_resource") or params.get("path") or metadata.get("target_resource") or "unknown"
    missing = list(raw.get("missing_dependencies") or [])
    required = list(raw.get("required_dependencies") or ["audit_logger"])
    available = list(raw.get("available_dependencies") or ["action_normalizer", "impact_classifier"])
    if target_backend == "unknown":
        missing.append("target_backend")
    if target_resource == "unknown":
        missing.append("target_resource")
    for dep in required:
        if dep not in available and dep not in missing:
            missing.append(dep)

    normalized = {
        "action_type": action_type,
        "target_backend": target_backend,
        "target_resource": target_resource,
        "action_params": params,
    }
    created_at = raw.get("created_at") or datetime.now(UTC).isoformat()
    description = raw.get("description") or f"{action_type} on {target_resource}"

    # Untrusted input (audit 002, AG-008). The profile is this component's descriptor of a known
    # action type, so declared effects, reversibility and blast radius can only raise it (``_floors``);
    # for an action type without a profile the declaration is all there is, so ``classify_impact``
    # never reads such an action as low risk, whatever it declares. Authority cannot be
    # observed from a request at all: a declared ``observed_authority`` stays in
    # ``proposed_inputs`` as a claim and does not satisfy ``required_authority``.
    return ActionCapsule(
        action_id=raw.get("action_id") or _stable_action_id(normalized),
        requested_by=raw.get("requested_by") or metadata.get("requested_by"),
        actor_type=raw.get("actor_type") or metadata.get("actor_type") or "agent",
        action_type=action_type,
        target_backend=target_backend,
        target_resource=target_resource,
        description=description,
        user_visible_summary=raw.get("user_visible_summary") or description,
        proposed_inputs=raw,
        normalized_inputs=normalized,
        affected_entities=list(raw.get("affected_entities") or ([target_resource] if target_resource != "unknown" else [])),
        affected_systems=list(raw.get("affected_systems") or ([target_backend] if target_backend != "unknown" else [])),
        required_dependencies=required,
        available_dependencies=available,
        missing_dependencies=sorted(set(missing)),
        **_floors(action_type, raw, _profile(action_type, {**raw, "action_params": params}, target_resource)),
        factual_preconditions=_normalize_preconditions(raw.get("factual_preconditions")),
        verification_contract=_normalize_verification_contract(raw.get("verification_contract")),
        coordination_context=_normalize_optional_model(raw.get("coordination_context")),
        world_state=_normalize_optional_model(raw.get("world_state")),
        hypothesis_context=_normalize_optional_model(raw.get("hypothesis_context")),
        created_at=created_at,
    )
