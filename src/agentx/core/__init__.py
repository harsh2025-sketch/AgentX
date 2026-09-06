"""Ownership boundary: ``agentx.core``.

Canonical responsibility: shared domain contracts and Agent Runtime primitives
that other subsystems build on.

This package is a low-level, non-authority foundation. It may hold contracts and
runtime primitives; it does not implement the Trusted Kernel, capabilities,
Hive storage, or any adaptive subsystem.

Status:

    - ``ids`` — canonical opaque domain identifiers (A1.04).
    - ``errors`` — structured error model and taxonomy (A1.04).
    - ``result`` — typed Success/Failure result abstraction (A1.04).
    - ``tasks`` — canonical immutable Task schema, controlled status and
      priority vocabulary, and deterministic serialization (A1.05). Data
      model only: the Task state machine is A1.06 and the Task Manager is
      owned by Day 2.
    - ``task_state`` — canonical Task status transition contract (A1.06):
      the explicit legal transition matrix, terminal-state semantics, and
      pure/stateless helpers to ask, validate, and derive a transitioned
      Task. Transitions only: no Task Manager, execution, scheduling,
      cancellation, or persistence.
    - ``task_decomposition`` — canonical hierarchical task decomposition
      contract (A6.01): immutable ``DecompositionNode`` /
      ``TaskDecomposition`` records representing a high-level Task broken
      into smaller task units (root/subtask identity, single-parent
      tree, objectives, declarative success criteria, ordering
      constraints, bounded node count and depth, deterministic
      serialization) plus the inert ``accept_model_proposal`` boundary
      that validates untrusted model/reasoner output into those typed
      structures. Plan/decomposition data only: no dependency planning
      (A6.02), no full plan validation (A6.04), no execution, no
      authority, and no success claims.
    - ``events`` — canonical immutable Event envelope, taxonomy, payloads,
      validation, and deterministic serialization (C1.02 / A1.02b).
    - ``knowledge`` — canonical immutable KnowledgeRecord contract for
      semantic/knowledge data: identity, type, status/trust vocabulary,
      minimum provenance hook, and typed scope (C2.02). Records only: Hive
      lifecycle policy and retrieval live in ``agentx.hive`` tasks; SQLite
      persistence lives in ``agentx.infrastructure.knowledge_store``.
    - ``procedures`` — canonical immutable ProcedureRecord contract for stored
      procedure revisions: identity plus explicit revision, storage-level
      status, typed scope, and an opaque payload hook (C2.03). Records only:
      the Procedure Graph IR is owned by A3.01 and candidate-skill lifecycle
      by C3.09; SQLite persistence lives in
      ``agentx.infrastructure.procedure_store``.
    - ``causal_experience`` — canonical immutable C2.10 representation of one
      observed execution transition: state-before, requested action,
      observation, state-after, verification, and historical outcome. Records
      only: no causal inference, authority, execution, learning, routing, or
      persistence.
    - ``failure_taxonomy`` — canonical closed failure vocabulary
      (``FailureCategory``) plus the minimal immutable ``FailureClassification``
      record: category, inert summary/detail, optional canonical references to
      already-existing failure evidence, and deterministic serialization
      (C4.01). Vocabulary and representation only: no diagnosis, localization,
      inference from text, repair, retry, fallback, escalation, suppression,
      authority, or persistence.
    - ``failure_localization`` — canonical closed localization-target vocabulary
      (``FailureLocationKind``) plus explicit ``LocalizationEvidence`` and the
      minimal immutable ``FailureLocalization`` record: where structured
      evidence points in the execution chain, optional linkage to a C4.01
      classification, and deterministic serialization (C4.02). Localization
      only: no diagnosis, keyword/model inference, trajectory analysis, repair,
      retry, authority, or persistence. Category and location remain orthogonal.
    - ``failure_diagnosis`` — canonical closed procedure-node diagnosis boundary
      (C4.03): explicit structured ``DiagnosticEvidence`` (typed kinds bound to
      canonical references), the closed ``DiagnosticConclusion`` vocabulary
      (``UNKNOWN`` fail-closed default, explicit ``NODE_IMPLICATED``), and the
      minimal immutable ``FailureDiagnosis`` record embedding a C4.01
      classification and a C4.02 ``PROCEDURE_NODE`` localization by value.
      Representation only: no inference, no keyword/stack/model diagnosis, no
      root-cause proof, no repair/retry/execution/authority surface, no
      persistence, and no mutation of the embedded C4.01/C4.02 records.
    - ``repair_candidates`` — canonical closed repair-candidate boundary (C4.04):
      the tiny ``RepairCandidateKind`` vocabulary (``UNKNOWN`` fail-closed
      default, explicit ``NODE_DEFINITION_REVISION``) and the minimal immutable
      ``RepairCandidate`` record embedding the exact C4.03 ``FailureDiagnosis``
      by value with strict position links to its evidence, plus the pure
      deterministic ``derive_repair_candidates`` packaging. Hypotheses only:
      a candidate is never correct, safe, selected, authorized, executed, or
      verified; no inference from text, no ranking or scoring, no repair,
      patching, retry, execution, authority, persistence, or mutation of the
      embedded diagnosis.
"""
