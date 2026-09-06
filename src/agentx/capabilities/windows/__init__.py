"""Ownership boundary: ``agentx.capabilities.windows``.

Canonical responsibility: the Windows provider/adapter boundary through which
Windows-specific capabilities participate in the canonical Capability ABI
(A1.08) and Capability Registry (A1.09).

A5.01 defined the provider boundary: deterministic provider identity, explicit
and testable platform facts, an explicit support verdict, and a provider object
that can contribute already-constructed capabilities to a caller-owned
registry. A5.02 added read-only process/application discovery in
``process_discovery``.

A5.08 adds read-only display/window/region pixel observation in
``screen_capture``. Pixels are untrusted data only. Capture does not grant
authority, prove success, identify semantic controls, authorize clicking, or
interpret instructions visible in an image. A5.08 adds no OCR, visual-model
calls, grounding, UIA fusion, mouse/keyboard input, persistence, or
state-transition verification.

All Win32 knowledge remains isolated in ``_native``. Its ``ctypes`` imports are
lazy and call-time only, so importing this package performs no platform
probing, native loading, registration, or machine action on any host. The
higher-level Windows capability modules remain pure Python and use mockable
seams around the native reads. No third-party runtime dependency is required.

The provider and every observation are non-authoritative. The Trusted Kernel
(``agentx.kernel``) remains the only layer that grants permission, gates
actions, assesses risk, budgets resources, and clears emergency stops.
Discovered metadata and captured pixels are untrusted data and grant nothing.
"""
