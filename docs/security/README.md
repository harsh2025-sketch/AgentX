# AgentX security documentation

C9.01 is the canonical whole-system threat model for the *landed*
architecture. It is an engineering artifact, not a runtime security
subsystem and not an authority boundary.

| Document | Role |
| --- | --- |
| [`threat-model.md`](threat-model.md) | Human-readable model: assets, trust boundaries, attacker classes, invariants, and every threat |
| [`threat-catalogue.json`](threat-catalogue.json) | Machine-readable catalogue with stable IDs `AX-T-NNN` for C9.02–C9.08 |

Existing C1.09 kernel-contract notes stay at
[`docs/security_boundaries.md`](../security_boundaries.md). Do not treat
this directory as executable policy: the Trusted Kernel remains
`src/agentx/kernel`.

Architecture tests in `tests/architecture/test_threat_catalogue.py` pin
catalogue schema, unique IDs, referenced source/test paths, invariant
coverage, and markdown/JSON agreement.
