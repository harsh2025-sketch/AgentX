"""Ownership boundary: ``agentx.capabilities.windows``.

Canonical responsibility: the Windows provider/adapter boundary through which
future Windows-specific capabilities (A5.02+) participate in the canonical
Capability ABI (A1.08) and Capability Registry (A1.09).

A5.01 defined *only* that boundary in
:mod:`agentx.capabilities.windows.provider`: deterministic provider identity,
explicit and testable platform facts, an explicit support verdict, and a
provider object that can contribute already-constructed capabilities to a
caller-owned registry. A5.01 implemented no Windows automation: no Win32, COM,
UI Automation, pywinauto, input injection, clipboard, screenshots, OCR, or app
launching. A5.02 added the first read-only native surface (process snapshot,
image-path query, top-level window walk); everything else — UI Automation
traversal, controls, input, capture, dialogs, app launching — still belongs to
later tasks.

A5.02 adds read-only process/application discovery in
``agentx.capabilities.windows.process_discovery`` (typed process/window
identities with explicit metadata-availability semantics, deterministic
normalization, and the operation wrapped as an ordinary canonical Capability).
The only Win32 knowledge lives in the isolated ``_native`` module, whose
``ctypes`` imports are lazy and call-time only, so package import still
performs no platform detection, no registration, no native import, and no
machine action on any host. It has zero third-party dependencies.

The provider is not authority. Availability is descriptive: the Trusted Kernel
(``agentx.kernel``) remains the only layer that grants permission, gates
actions, assesses risk, budgets resources, and clears emergency stops.
Discovered process/window metadata is untrusted data and grants nothing.
"""
