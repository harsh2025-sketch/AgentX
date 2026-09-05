"""Ownership boundary: ``agentx.capabilities.windows``.

Canonical responsibility: the Windows provider/adapter boundary through which
future Windows-specific capabilities (A5.02+) participate in the canonical
Capability ABI (A1.08) and Capability Registry (A1.09).

A5.01 defines *only* that boundary in
:mod:`agentx.capabilities.windows.provider`: deterministic provider identity,
explicit and testable platform facts, an explicit support verdict, and a
provider object that can contribute already-constructed capabilities to a
caller-owned registry. It implements no Windows automation: no Win32, COM, UI
Automation, pywinauto, input injection, clipboard, screenshots, OCR, process or
window enumeration, and no app launching. Those belong to A5.02 and later.

Importing this package performs no platform detection, no registration, and no
native import. It has zero third-party dependencies.

The provider is not authority. Availability is descriptive: the Trusted Kernel
(``agentx.kernel``) remains the only layer that grants permission, gates
actions, assesses risk, budgets resources, and clears emergency stops.
"""
