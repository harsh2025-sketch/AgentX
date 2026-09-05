"""Canonical human operating-mode vocabulary for AgentX (A6.07).

Human operating modes are explicit human control intent carried as inert data.
They describe how the human wants the current interaction to be treated; they
are not permissions, policy, trust, execution, verification, or lifecycle
state.

Semantics:

* ``NORMAL`` — ordinary governed AgentX operation.
* ``LEARN`` — the human indicates that the current interaction may be useful as
  future learning material. It does not capture, store, promote, or learn
  anything by itself.
* ``TEACH`` — the human intentionally demonstrates or explains a workflow or
  fact for future learning consideration. It does not make that material
  trusted, verified, or executable.
* ``DEBUG`` — the human requests increased diagnostic/development
  observability. It does not disable or weaken security policy.

Changing or carrying a mode never grants Permission, creates or strengthens an
AuthorityContext, bypasses ActionGate, lowers RiskLevel, enlarges or resets a
ResourceEnvelope, clears EmergencyStop, authorizes research/network/arbitrary
code, enables destructive actions, verifies knowledge or outcomes, activates a
Procedure, changes Task state, or suppresses verification.

The vocabulary is deliberately the whole A6.07 contract. There is no mutable
"current mode" singleton, router, persistence model, UI state, prompt contract,
learning capture, or execution hook here. A later owner may carry a selected
``HumanOperatingMode`` through an appropriate runtime/request contract without
changing these semantics.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final

__all__ = [
    "CANONICAL_HUMAN_OPERATING_MODES",
    "HumanOperatingMode",
]


class HumanOperatingMode(StrEnum):
    """Closed canonical vocabulary for human operating intent.

    The lowercase values are the deterministic serialized representation. The
    enum is intentionally non-ordered: no mode is more trusted, privileged, or
    authoritative than another.
    """

    NORMAL = "normal"
    LEARN = "learn"
    TEACH = "teach"
    DEBUG = "debug"


#: Canonical modes in declaration order. Exported so callers can enumerate the
#: closed vocabulary without re-declaring it.
CANONICAL_HUMAN_OPERATING_MODES: Final[tuple[HumanOperatingMode, ...]] = tuple(HumanOperatingMode)
