# AX-124 canonical scope audit

Canonical `KnowledgeScope` currently has these structured dimensions: application, application version, operating system, environment, project, and context.

The completion path treats those fields as exact applicability metadata. The existing context field is opaque structured applicability supplied by the caller; protected retrieval does not parse it or accept content/provenance as a substitute. Therefore user/task/device/session/procedure/security/trust distinctions are safe only when the composing subsystem supplies them as part of its canonical context identifier. If that distinction is absent, the protected retrieval boundary does not guess or widen scope.

This is deliberate fail-closed behavior. Adding first-class new `ScopeDimension` enum members is a schema/contract expansion that should happen only when those identities have canonical cross-subsystem types; AX-124 does not invent duplicate identity systems.
