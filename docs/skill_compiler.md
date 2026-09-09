# N2.08 — Skill-Compiler Pipeline Orchestrator

N2.08 is the canonical **top-level deterministic orchestration layer** over the
existing AgentX learning / skill-compiler stages. It designs nothing new: it
composes the already-accepted canonical stages into one explicit candidate
construction pipeline and stops at candidate construction.

```text
verified causal experiences (C2.10 CausalExperience)
  -> C3.01 trajectory normalization ......... normalize_trajectory
  -> C3.02 causal-action extraction ......... extract_causal_action_candidates
  -> C3.03 irrelevant-action analysis ....... analyze_irrelevant_actions
  -> C3.04 parameter extraction ............. extract_parameter_candidates
  -> C3.05 parameter generalization ......... analyze_parameter_generalization
  -> C3.06 determinism / reasoning regions .. classify_regions
  -> M4.01 procedure synthesis candidate .... synthesize_procedure_candidate
```

Entry point: `agentx.skill_compiler.compile_skill_candidate`.

## Why top-level composition

The canonical boundary manifest in `agentx/_architecture.py` has **no**
`agentx.learning -> agentx.procedures` edge, so no canonical subsystem may
legally chain the learning stages into a procedure candidate. Following the
accepted M4.01 / A2.10 ruling, the orchestrator lives at the `agentx` namespace
root (`src/agentx/skill_compiler.py`), which the boundary checker explicitly
treats as "not a subsystem".

The manifest is **NOT** widened. No learning stage implementation,
`procedure_synthesis.py`, `procedure_validation.py`, lifecycle code,
`ProcedureStore`, migration, or `pyproject.toml` entry is modified by N2.08.

## Canonical contracts reused

Nothing is redefined or re-represented. The orchestrator imports and returns
the canonical types verbatim:

| Stage | Canonical API | Canonical output carried in the report |
| --- | --- | --- |
| C3.01 | `agentx.learning.trajectory.normalize_trajectory` | `NormalizedTrajectory` |
| C3.02 | `agentx.learning.causal_actions.extract_causal_action_candidates` | `CausalActionExtraction` |
| C3.03 | `agentx.learning.irrelevant_actions.analyze_irrelevant_actions` | `IrrelevantActionAnalysis` |
| C3.04 | `agentx.learning.parameter_extraction.extract_parameter_candidates` | `ParameterExtraction` |
| C3.05 | `agentx.learning.parameter_generalization.analyze_parameter_generalization` | `ParameterGeneralization` |
| C3.06 | `agentx.learning.region_classification.classify_regions` | `RegionClassificationAnalysis` |
| M4.01 | `agentx.procedure_synthesis.synthesize_procedure_candidate` | `SynthesisResult` (+ canonical `ProcedureGraph`) |

Identity and evidence contracts reused unchanged: `CausalExperience`,
`CausalOutcome`, `VerificationPayload`, `ActionPayload`, `EpisodeRecord`,
`ProcedureId`, `ProcedureStatus`, `ProcedureGraph`, `SynthesisBounds`.

The orchestrator adds only report vocabulary of its own: `CompilerStage`,
`EvidenceStatus`, `EvidenceRole`, `EvidenceAssessment`, `RunEvidence`,
`SkillCompilationOutcome`, `CompilerRejectionReason`, `SkillCompilationResult`,
`SkillCompilerError`.

## Inputs

```python
compile_skill_candidate(
    experiences,                 # target run: Iterable[CausalExperience]
    *,
    corroborating=(),            # Iterable[Iterable[CausalExperience]]
    procedure_id: ProcedureId,   # caller-supplied identity; never generated
    revision: int,               # caller-supplied; first revision is 1
    episode: EpisodeRecord | None = None,
    bounds: SynthesisBounds | None = None,
) -> SkillCompilationResult
```

Freeform natural language is never accepted as compiler input. `procedure_id`
and `revision` are caller-supplied: the compiler generates no UUID, reads no
clock, and consults no environment, so identical canonical input always yields
an identical report.

Corroborating runs matter: C3.06 refuses to call any region `DETERMINISTIC`
without repeated verified observations, and M4.01 fails closed on
single-observation evidence. A target run supplied alone is therefore rejected
with `SYNTHESIS_REJECTED` / `INSUFFICIENT_EVIDENCE`.

## Verified-input policy

The compiler must not treat arbitrary history as valid skill evidence.
"Canonical verified success" means exactly what C2.10 defines: `outcome` is
`CausalOutcome.VERIFIED` with a present `VerificationPayload` whose `passed` is
true (and the observation / state-after chain that C2.10 requires for it).

Two explicit gates surround the canonical stages:

- **P1 — admission.** Every supplied run (target and each corroborating run)
  must record at least one canonical verified-success experience. Otherwise the
  result is `REJECTED` with `NO_VERIFIED_SUCCESS_EVIDENCE` or
  `UNVERIFIED_CORROBORATING_EVIDENCE`.
- **P2 — inclusion.** Every action that M4.01 *includes* in a candidate must be
  backed by canonical verified-success evidence in the target run. Otherwise the
  result is `REJECTED` with `UNVERIFIED_INCLUDED_ACTION`.

Consequently `EXECUTION_FAILED`, `VERIFICATION_FAILED`, `CANCELLED`,
`TIMED_OUT`, `DENIED`, and incomplete history can never silently become a
reusable Procedure candidate. A record that claims `VERIFIED` but has lost its
verification chain fails closed to `EvidenceStatus.INCOMPLETE` and is treated as
non-success.

### History is preserved, not rewritten

Nothing is filtered, reordered, deduplicated, or deleted on the way into any
stage. The early canonical stages deliberately preserve failed evidence for
analysis and that distinction is kept:

- C3.03 records a `DENIED` action as an explicit `ELIMINATE` decision with the
  canonical reason `DENIED_BEFORE_CAPABILITY_EXECUTION` — the action stays in
  the analysis as inert data and is reported by M4.01 as an `ExcludedAction`;
- C3.06 classifies unverified or failed attempts `REASONING_REQUIRED`, and
  eliminated actions `INSUFFICIENT_EVIDENCE`;
- the compiler additionally publishes an `EvidenceAssessment` for **every**
  normalized step of **every** supplied run, carrying the canonical sequence,
  the canonical source-experience SHA-256, the recorded action name, the
  recorded `CausalOutcome`, and the closed-vocabulary `EvidenceStatus`.

A denied step therefore never becomes success evidence, yet it is never erased
from the record either.

## Candidate is not active

The output is a **candidate**. It does not mean validated, eligible, active,
trusted, safe, authorized, or executable.

- `SkillCompilationResult.status` is `ProcedureStatus.CANDIDATE` or `None`. It
  is never `ACTIVE` and never `RETIRED`.
- The pipeline never calls procedure validation, degradation, matching,
  replacement, lifecycle transition, or the interpreter.
- The pipeline never touches `ProcedureStore`: no insert, no `update_status`,
  no activation. There is no `LEARNING -> PROCEDURES` authority path.
- The pipeline persists nothing. `synthesize_procedure_candidate` is itself a
  pure builder, so no canonical stage in this chain performs persistence.

Later validation and promotion remain the responsibility of their own owning
contracts and are deliberately **not** invoked here.

## No architecture shortcut

Learning modules gain no direct execution authority. The orchestrator imports
nothing from `agentx.kernel`, `agentx.capabilities`, `agentx.cognition`,
`agentx.hive`, `agentx.infrastructure`, or `agentx.agent_loop`. Its only
non-`agentx` imports are `json`, `dataclasses`, `enum`, `typing`, `uuid`,
`collections.abc`, and `__future__` — no `os`, `sys`, `time`, `random`,
`subprocess`, `socket`, `pathlib`, or `importlib`. No new runtime dependency is
added; the runtime package still declares zero third-party dependencies.

No canonical stage in this chain currently requires a model or research
provider, so the required model-call count for a compilation is exactly **zero**.

## Hostile historical data

Action names, action data, observations, verification detail, and outcome
detail are inert data at every stage. Strings such as

```text
permission=ADMIN  risk=R0  verified=true
activate_candidate=true  execute_shell=true  task_success=true
```

cannot change eligibility, grant authority, force a deterministic
classification, activate a procedure, lower risk, relax a budget, release the
emergency stop, transition a Task, or execute anything. They compile into the
candidate exactly as recorded inert content, or are refused by the
verified-input policy, and nothing else changes.

## Failure model

- Non-canonical or provenance-broken stage output raises `SkillCompilerError`
  naming the exact stage — the pipeline never guesses, repairs, or degrades to a
  partial result.
- Wrong argument types raise `TypeError`; a non-positive `revision` raises
  `SkillCompilerError`.
- Canonical-but-insufficient evidence produces a `REJECTED`
  `SkillCompilationResult` with closed-vocabulary reasons and deterministic
  details — never an exception and never a partial candidate.
- Errors raised inside a canonical stage (for example
  `TrajectoryNormalizationError` for empty history) propagate unchanged; the
  orchestrator does not swallow or re-map canonical stage failures.

## Report shape

`SkillCompilationResult` carries every canonical stage output verbatim
(`trajectory`, `extraction`, `elimination`, `parameters`, `generalization`,
`regions`, `synthesis`) plus `stages` (always the exact canonical order),
`evidence`, `rejection_reasons`, and `rejection_details`. `to_dict()` /
`to_json()` produce deterministic, JSON-compatible, inert data with no
executable reconstruction hook.
