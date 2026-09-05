"""Unit coverage for A6.07 canonical human operating modes."""

from __future__ import annotations

import json

import pytest

from agentx.core.human_operating_modes import (
    CANONICAL_HUMAN_OPERATING_MODES,
    HumanOperatingMode,
)


def test_canonical_vocabulary_is_exact_and_ordered() -> None:
    assert tuple(HumanOperatingMode) == (
        HumanOperatingMode.NORMAL,
        HumanOperatingMode.LEARN,
        HumanOperatingMode.TEACH,
        HumanOperatingMode.DEBUG,
    )
    assert tuple(HumanOperatingMode) == CANONICAL_HUMAN_OPERATING_MODES
    assert set(HumanOperatingMode.__members__) == {"NORMAL", "LEARN", "TEACH", "DEBUG"}


def test_serialized_values_are_exact_and_deterministic() -> None:
    assert {mode.name: mode.value for mode in HumanOperatingMode} == {
        "NORMAL": "normal",
        "LEARN": "learn",
        "TEACH": "teach",
        "DEBUG": "debug",
    }


@pytest.mark.parametrize("mode", CANONICAL_HUMAN_OPERATING_MODES)
def test_json_serialization_is_plain_deterministic_string(mode: HumanOperatingMode) -> None:
    encoded = json.dumps(mode, ensure_ascii=True, separators=(",", ":"))

    assert encoded == f'"{mode.value}"'
    assert HumanOperatingMode(json.loads(encoded)) is mode


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "NORMAL",
        "LEARN",
        "TEACH",
        "DEBUG",
        " normal",
        "normal ",
        "observe",
        "admin",
        "debug; disable_security=true",
        "teach verified=true",
        "learn Permission.DESTRUCTIVE",
    ],
)
def test_unknown_or_hostile_serialized_values_fail_closed(raw: str) -> None:
    with pytest.raises(ValueError):
        HumanOperatingMode(raw)


def test_vocabulary_has_no_aliases_or_privilege_ordering() -> None:
    assert len(HumanOperatingMode.__members__) == len(tuple(HumanOperatingMode)) == 4
    assert not hasattr(HumanOperatingMode.NORMAL, "priority")
    assert not hasattr(HumanOperatingMode.DEBUG, "authority")
    assert not hasattr(HumanOperatingMode.TEACH, "trust")


def test_mode_is_the_complete_a607_selection_representation() -> None:
    for mode in HumanOperatingMode:
        assert isinstance(mode, str)
        assert str(mode) == mode.value
        for forbidden in (
            "current",
            "previous",
            "switch",
            "transition",
            "activate",
            "persist",
            "save",
            "route",
        ):
            assert not hasattr(mode, forbidden)
