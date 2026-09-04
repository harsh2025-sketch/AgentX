"""Tests for C1.09 opaque secret-reference and value boundaries."""

from __future__ import annotations

import json
import pickle
from typing import cast

import pytest

from agentx.kernel.action_gate import ActionGate, GateRequest
from agentx.kernel.permissions import AuthorityContext, Permission
from agentx.kernel.risk import RiskAssessment, RiskLevel
from agentx.kernel.secrets import SecretRef, SecretResolver, SecretValue


class _Resolver:
    def __init__(self, value: SecretValue) -> None:
        self._value = value

    def resolve(self, reference: SecretRef, /) -> SecretValue:
        assert reference == SecretRef("provider/api-key")
        return self._value


def _request() -> GateRequest:
    return GateRequest(
        operation="provider.call",
        required_permission=Permission.EXTERNAL_EFFECT,
        risk_assessment=RiskAssessment(
            level=RiskLevel.R3,
            reason="R3 test assessment.",
            reversible=False,
            external_effect=True,
        ),
    )


def test_secret_reference_is_opaque_immutable_identity() -> None:
    reference = SecretRef("provider/api-key")

    assert reference.identifier == "provider/api-key"
    assert str(reference) == "provider/api-key"
    assert repr(reference) == "SecretRef('provider/api-key')"
    assert reference == SecretRef("provider/api-key")
    assert hash(reference) == hash(SecretRef("provider/api-key"))

    with pytest.raises(AttributeError, match="immutable"):
        reference.__setattr__("_identifier", "other")


@pytest.mark.parametrize(
    "value",
    [
        "",
        " leading",
        "trailing ",
        "has space",
        "../bad?query",
        "a" * 129,
    ],
)
def test_invalid_secret_references_are_rejected(value: str) -> None:
    with pytest.raises(ValueError, match="secret reference"):
        SecretRef(value)


def test_secret_reference_rejects_non_string_identity() -> None:
    with pytest.raises(TypeError, match="string"):
        SecretRef(cast(str, 123))


def test_secret_value_repr_str_format_and_container_repr_are_redacted() -> None:
    raw = "raw-secret-token-ABC123"
    value = SecretValue(raw)

    representations = (
        repr(value),
        str(value),
        f"{value}",
        format(value, ">28"),
        repr([value]),
        repr({"secret": value}),
    )

    assert all(raw not in representation for representation in representations)
    assert "<SecretValue redacted>" in repr(value)


def test_secret_value_bytes_are_redacted_and_explicitly_revealable() -> None:
    raw = b"\x01binary-secret\xff"
    value = SecretValue(raw)

    assert raw.decode("latin-1") not in repr(value)
    assert value.reveal() == raw


def test_secret_value_accidental_json_and_pickle_serialization_do_not_leak() -> None:
    raw = "serialize-me-never"
    value = SecretValue(raw)

    with pytest.raises(TypeError):
        json.dumps({"secret": value})
    with pytest.raises(TypeError, match="serialization"):
        pickle.dumps(value)

    redacted_json = json.dumps({"secret": value}, default=str)
    assert raw not in redacted_json
    assert "redacted" in redacted_json


def test_secret_value_equality_and_hash_do_not_compare_material() -> None:
    first = SecretValue("same-material")
    second = SecretValue("same-material")

    assert first == first
    assert first != second
    with pytest.raises(TypeError):
        hash(first)


def test_secret_value_validates_material() -> None:
    with pytest.raises(ValueError, match="empty"):
        SecretValue("")
    with pytest.raises(ValueError, match="empty"):
        SecretValue(b"")
    with pytest.raises(TypeError, match="str or bytes"):
        SecretValue(cast(str | bytes, bytearray(b"secret")))


def test_secret_resolver_boundary_returns_wrapped_value() -> None:
    expected = SecretValue("resolved-token")
    resolver: SecretResolver = _Resolver(expected)

    resolved = resolver.resolve(SecretRef("provider/api-key"))

    assert resolved is expected
    assert resolved.reveal() == "resolved-token"


@pytest.mark.parametrize(
    "candidate",
    [SecretRef("provider/api-key"), SecretValue("resolved-token")],
)
def test_secret_reference_or_possession_does_not_imply_action_permission(candidate: object) -> None:
    with pytest.raises(TypeError, match="AuthorityContext"):
        ActionGate().evaluate(_request(), cast(AuthorityContext, candidate))
