"""Safe binding of compiled skill DATA to canonical governed capability requests.

This module closes the M3 integration seam between the learning compiler and
the existing L2 procedure runtime without making procedure text executable.

The skill compiler deliberately emits inert ACTION metadata: an observed action
name/data object plus evidence-backed constants and generalized parameter
descriptors.  That representation carries no Capability identity because the
learning layer is not allowed to invent one.

A trusted top-level composition root may materialize that inert graph by
supplying an *exact* mapping from observed action names to canonical
CapabilityIdentity values.  Parameter binding remains data-only until a
caller-supplied trusted request factory constructs a typed CapabilityRequest.
The resulting request still flows through Executor -> ActionGate -> governed
capability execution.  No permission, risk, budget, stop, verification, or
lifecycle authority can be created here.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from types import MappingProxyType
from typing import Any

from agentx.capabilities.abi import (
    CapabilityIdentity,
    CapabilityName,
    CapabilityRequest,
    CapabilityVersion,
)
from agentx.procedures.graph import ProcedureGraph, ProcedureNode, ProcedureNodeKind
from agentx.procedures.nodes import ActionNodeSpec

__all__ = [
    "CapabilityRequestFactory",
    "CompiledSkillBindingError",
    "build_compiled_action_requests",
    "materialize_compiled_procedure_graph",
    "resolve_compiled_action_data",
]


class CompiledSkillBindingError(ValueError):
    """Raised when inert compiled data cannot be bound safely and exactly."""


type CapabilityRequestFactory = Callable[[Mapping[str, object]], CapabilityRequest[Any]]

_COMPILER_ACTION_FIELDS = frozenset(
    {
        "action_name",
        "action_data",
        "classification",
        "classification_reason",
        "constants",
        "parameters",
        "single_observation_fields",
        "source_sequence",
        "source_experience_sha256",
    }
)
_PARAMETER_FIELDS = frozenset({"parameter_name", "observed_values", "observation_count"})


def _copy_json(value: object, *, path: str) -> object:
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CompiledSkillBindingError(f"{path} contains a non-finite float")
        return value
    if isinstance(value, Mapping):
        copied: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise CompiledSkillBindingError(f"{path} contains a non-string object key")
            copied[key] = _copy_json(item, path=f"{path}.{key}")
        return copied
    if isinstance(value, tuple | list):
        return [_copy_json(item, path=f"{path}[{index}]") for index, item in enumerate(value)]
    raise CompiledSkillBindingError(f"{path} contains non-JSON data of type {type(value).__name__}")


def _json_object(value: object, *, path: str) -> dict[str, object]:
    copied = _copy_json(value, path=path)
    if not isinstance(copied, dict):
        raise CompiledSkillBindingError(f"{path} must be a JSON object")
    return copied


def _compiler_action_metadata(raw: Mapping[str, object]) -> dict[str, object]:
    metadata = _json_object(raw, path="compiled action metadata")
    actual = set(metadata)
    if actual != _COMPILER_ACTION_FIELDS:
        missing = sorted(_COMPILER_ACTION_FIELDS - actual)
        unknown = sorted(actual - _COMPILER_ACTION_FIELDS)
        details: list[str] = []
        if missing:
            details.append(f"missing={missing}")
        if unknown:
            details.append(f"unknown={unknown}")
        raise CompiledSkillBindingError(
            "ACTION does not contain exact compiler metadata"
            + (f": {', '.join(details)}" if details else "")
        )

    action_name = metadata["action_name"]
    if not isinstance(action_name, str) or not action_name or action_name != action_name.strip():
        raise CompiledSkillBindingError("compiled action_name must be non-empty and trimmed")
    _json_object(metadata["action_data"], path="compiled action_data")
    _json_object(metadata["constants"], path="compiled constants")
    _json_object(metadata["parameters"], path="compiled parameters")

    single = metadata["single_observation_fields"]
    if not isinstance(single, list):
        raise CompiledSkillBindingError("single_observation_fields must be a JSON array")
    if not all(isinstance(item, str) and item for item in single):
        raise CompiledSkillBindingError("single_observation_fields must contain non-empty strings")
    if len(single) != len(set(single)):
        raise CompiledSkillBindingError("single_observation_fields must not contain duplicates")

    sequence = metadata["source_sequence"]
    if type(sequence) is not int or sequence < 1:
        raise CompiledSkillBindingError("source_sequence must be a positive integer")
    fingerprint = metadata["source_experience_sha256"]
    if (
        not isinstance(fingerprint, str)
        or len(fingerprint) != 64
        or any(character not in "0123456789abcdef" for character in fingerprint)
    ):
        raise CompiledSkillBindingError(
            "source_experience_sha256 must be 64 lowercase hexadecimal characters"
        )
    return metadata


def _freeze_capability_bindings(
    bindings: Mapping[str, CapabilityIdentity],
) -> Mapping[str, CapabilityIdentity]:
    if not isinstance(bindings, Mapping):
        raise TypeError("capability_bindings must be a mapping")
    copied: dict[str, CapabilityIdentity] = {}
    for action_name, identity in bindings.items():
        if (
            not isinstance(action_name, str)
            or not action_name
            or action_name != action_name.strip()
        ):
            raise CompiledSkillBindingError(
                "capability binding keys must be non-empty trimmed action names"
            )
        if not isinstance(identity, CapabilityIdentity):
            raise TypeError("capability binding values must be CapabilityIdentity values")
        copied[action_name] = identity
    return MappingProxyType(copied)


def materialize_compiled_procedure_graph(
    graph: ProcedureGraph,
    capability_bindings: Mapping[str, CapabilityIdentity],
) -> ProcedureGraph:
    """Bind compiler ACTION names to explicit canonical capability identities.

    The caller, not procedure text, supplies the identity mapping.  The function
    only rewrites ACTION *representation* into the canonical ActionNodeSpec
    shape required by L2.  Evidence metadata remains inert under
    ActionNodeSpec.params and no CapabilityRequest is created or executed.
    """
    if not isinstance(graph, ProcedureGraph):
        raise TypeError(f"graph must be a ProcedureGraph, got {type(graph).__name__}")
    bindings = _freeze_capability_bindings(capability_bindings)

    used_names: set[str] = set()
    nodes: list[ProcedureNode] = []
    for node in graph.nodes:
        if node.kind is not ProcedureNodeKind.ACTION:
            nodes.append(node)
            continue

        metadata = _compiler_action_metadata(node.params)
        action_name = metadata["action_name"]
        assert isinstance(action_name, str)
        identity = bindings.get(action_name)
        if identity is None:
            raise CompiledSkillBindingError(
                f"no trusted capability identity binding for compiled action {action_name!r}"
            )
        used_names.add(action_name)
        nodes.append(
            ActionNodeSpec(
                capability_name=identity.name.value,
                capability_version=identity.version.to_str(),
                description="compiled from verified causal experience",
                params=metadata,
            ).to_node(node.id, label=node.label)
        )

    unused = sorted(set(bindings) - used_names)
    if unused:
        raise CompiledSkillBindingError(
            f"capability bindings contain actions not present in the compiled graph: {unused}"
        )

    return ProcedureGraph(
        entry=graph.entry,
        nodes=tuple(nodes),
        edges=graph.edges,
        schema_version=graph.schema_version,
    )


def _parameter_name(raw: object, *, field_name: str) -> str:
    if not isinstance(raw, Mapping):
        raise CompiledSkillBindingError(
            f"compiled parameter descriptor for {field_name!r} must be a JSON object"
        )
    descriptor = _json_object(raw, path=f"compiled parameter {field_name}")
    if set(descriptor) != _PARAMETER_FIELDS:
        raise CompiledSkillBindingError(
            f"compiled parameter descriptor for {field_name!r} has unexpected fields"
        )
    name = descriptor["parameter_name"]
    if not isinstance(name, str) or not name or name != name.strip():
        raise CompiledSkillBindingError(
            f"compiled parameter name for {field_name!r} must be non-empty and trimmed"
        )
    observed_values = descriptor["observed_values"]
    if not isinstance(observed_values, list) or len(observed_values) < 2:
        raise CompiledSkillBindingError(
            f"compiled parameter {name!r} requires at least two observed values"
        )
    count = descriptor["observation_count"]
    if type(count) is not int or count != len(observed_values):
        raise CompiledSkillBindingError(
            f"compiled parameter {name!r} observation_count is inconsistent"
        )
    return name


def _freeze_parameter_binding(
    binding: Mapping[str, object],
) -> Mapping[str, object]:
    if not isinstance(binding, Mapping):
        raise TypeError("parameter_binding must be a mapping")
    copied: dict[str, object] = {}
    for name, value in binding.items():
        if not isinstance(name, str) or not name or name != name.strip():
            raise CompiledSkillBindingError(
                "parameter binding keys must be non-empty trimmed strings"
            )
        copied[name] = _copy_json(value, path=f"parameter_binding.{name}")
    return MappingProxyType(copied)


def resolve_compiled_action_data(
    graph: ProcedureGraph,
    parameter_binding: Mapping[str, object],
) -> Mapping[str, Mapping[str, object]]:
    """Resolve evidence-backed generalized parameters into inert action data.

    Missing and unknown parameters fail closed.  Constants and original
    single-observation fields are preserved exactly.  This function still
    returns DATA; typed CapabilityParams are constructed only by trusted
    request factories at the next boundary.
    """
    if not isinstance(graph, ProcedureGraph):
        raise TypeError(f"graph must be a ProcedureGraph, got {type(graph).__name__}")
    binding = _freeze_parameter_binding(parameter_binding)

    parsed: list[tuple[str, dict[str, object]]] = []
    required: set[str] = set()
    for node in graph.nodes:
        if node.kind is not ProcedureNodeKind.ACTION:
            continue
        try:
            spec = ActionNodeSpec.from_node(node)
        except ValueError as exc:
            raise CompiledSkillBindingError(
                f"ACTION node {node.id.to_str()!r} is not a canonical materialized action"
            ) from exc
        metadata = _compiler_action_metadata(spec.params)
        parameters = _json_object(metadata["parameters"], path="compiled parameters")
        for field_name, raw_descriptor in parameters.items():
            required.add(_parameter_name(raw_descriptor, field_name=field_name))
        parsed.append((node.id.to_str(), metadata))

    missing = sorted(required - set(binding))
    unknown = sorted(set(binding) - required)
    if missing or unknown:
        details: list[str] = []
        if missing:
            details.append(f"missing={missing}")
        if unknown:
            details.append(f"unknown={unknown}")
        raise CompiledSkillBindingError(
            "parameter binding does not exactly match compiled requirements: " + ", ".join(details)
        )

    resolved_by_node: dict[str, Mapping[str, object]] = {}
    for node_id, metadata in parsed:
        action_data = _json_object(metadata["action_data"], path=f"{node_id}.action_data")
        constants = _json_object(metadata["constants"], path=f"{node_id}.constants")
        parameters = _json_object(metadata["parameters"], path=f"{node_id}.parameters")
        single_raw = metadata["single_observation_fields"]
        assert isinstance(single_raw, list)
        single = set(single_raw)

        constant_fields = set(constants)
        parameter_fields = set(parameters)
        if constant_fields & parameter_fields:
            raise CompiledSkillBindingError(
                f"{node_id}: a field cannot be both constant and parameterized"
            )
        if (constant_fields | parameter_fields | single) - set(action_data):
            raise CompiledSkillBindingError(
                f"{node_id}: compiler metadata references a field absent from action_data"
            )

        resolved = dict(action_data)
        for field_name, value in constants.items():
            resolved[field_name] = value
        for field_name, raw_descriptor in parameters.items():
            name = _parameter_name(raw_descriptor, field_name=field_name)
            resolved[field_name] = binding[name]
        resolved_by_node[node_id] = MappingProxyType(resolved)

    return MappingProxyType(resolved_by_node)


def _identity_from_spec(spec: ActionNodeSpec) -> CapabilityIdentity:
    return CapabilityIdentity(
        name=CapabilityName(spec.capability_name),
        version=CapabilityVersion.from_str(spec.capability_version),
    )


def build_compiled_action_requests(
    graph: ProcedureGraph,
    parameter_binding: Mapping[str, object],
    request_factories: Mapping[CapabilityIdentity, CapabilityRequestFactory],
) -> Mapping[str, CapabilityRequest[Any]]:
    """Construct typed requests using trusted factories, never procedure code.

    Factories are supplied by the composition root and keyed by exact canonical
    CapabilityIdentity.  Procedure content cannot select a permission, change a
    risk level, widen a budget, clear stop state, or execute anything.  The
    returned requests are inert until the canonical Executor receives them.
    """
    if not isinstance(request_factories, Mapping):
        raise TypeError("request_factories must be a mapping")
    resolved = resolve_compiled_action_data(graph, parameter_binding)
    requests: dict[str, CapabilityRequest[Any]] = {}

    for node in graph.nodes:
        if node.kind is not ProcedureNodeKind.ACTION:
            continue
        try:
            spec = ActionNodeSpec.from_node(node)
        except ValueError as exc:
            raise CompiledSkillBindingError(
                f"ACTION node {node.id.to_str()!r} is not a canonical materialized action"
            ) from exc
        identity = _identity_from_spec(spec)
        factory = request_factories.get(identity)
        if factory is None:
            raise CompiledSkillBindingError(f"no trusted request factory for capability {identity}")
        if not callable(factory):
            raise TypeError("request factory values must be callable")

        try:
            request = factory(resolved[node.id.to_str()])
        except Exception as exc:
            raise CompiledSkillBindingError(
                f"trusted request factory rejected parameters for {identity}"
            ) from exc
        if not isinstance(request, CapabilityRequest):
            raise TypeError("request factory must return a CapabilityRequest")
        if request.identity != identity:
            raise CompiledSkillBindingError(
                f"request factory returned identity {request.identity}, expected {identity}"
            )
        requests[node.id.to_str()] = request

    return MappingProxyType(requests)
