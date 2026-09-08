"""Architecture guardrails for the N2.07 top-level L5 exploratory boundary.

The boundary is a *composition* module: it composes canonical contracts (A2.10
strategy port, A2.09 anti-loop, A2.03 reasoner, A4.01 gap assessment, A4.02-A4.04
research contracts, A1.07 execution context, C1.08 budget, C1.09 stop) and owns no
decision of its own. These tests pin that composition so a later edit cannot
silently turn bounded research into an execution, verification, promotion, or
budget authority - or into a new subsystem with new edges.
"""

from __future__ import annotations

import ast
from pathlib import Path

from agentx import _architecture

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _REPO_ROOT / "src"
_AGENTX_ROOT = _SRC_ROOT / "agentx"
_BOUNDARY = _AGENTX_ROOT / "exploratory_strategy.py"
_AGENT_LOOP = _AGENTX_ROOT / "agent_loop.py"
_CAPABILITY_STRATEGY = _AGENTX_ROOT / "capability_strategy.py"
_PACKAGE_INIT = _AGENTX_ROOT / "__init__.py"
_ARCHITECTURE = _AGENTX_ROOT / "_architecture.py"
_PYPROJECT = _REPO_ROOT / "pyproject.toml"
_DOC = _REPO_ROOT / "docs" / "exploratory_strategy.md"

#: Every ``agentx`` module the boundary may import, exactly.
_ALLOWED_AGENTX_IMPORTS = frozenset(
    {
        "agentx.agent_loop",
        "agentx.cognition.anti_loop",
        "agentx.cognition.gap_detector",
        "agentx.cognition.model_provider",
        "agentx.cognition.reasoner",
        "agentx.cognition.research_acquisition",
        "agentx.cognition.research_objective",
        "agentx.cognition.research_provider",
        "agentx.cognition.router",
        "agentx.core.errors",
        "agentx.core.execution",
        "agentx.core.ids",
        "agentx.core.knowledge",
        "agentx.core.result",
        "agentx.core.tasks",
        "agentx.kernel.emergency_stop",
        "agentx.kernel.resource_budget",
    }
)

#: The whole non-``agentx`` import surface: inert typing, hashing, and chars.
_ALLOWED_STDLIB_IMPORTS = frozenset(
    {"__future__", "dataclasses", "enum", "hashlib", "string", "typing"}
)

_FORBIDDEN_IMPORT_ROOTS = frozenset(
    {
        "asyncio",
        "ctypes",
        "http",
        "importlib",
        "multiprocessing",
        "os",
        "pathlib",
        "playwright",
        "requests",
        "selenium",
        "shutil",
        "socket",
        "sqlite3",
        "subprocess",
        "sys",
        "threading",
        "uiautomation",
        "urllib",
        "win32api",
        "win32gui",
    }
)

#: Subsystems the boundary must never reach into: their internals own their own
#: decisions (execution, verification, procedures, memory, permissions, risk).
_FORBIDDEN_AGENTX_PREFIXES = (
    "agentx.browser",
    "agentx.capabilities",
    "agentx.hive",
    "agentx.infrastructure",
    "agentx.kernel.action_gate",
    "agentx.kernel.audit",
    "agentx.kernel.permissions",
    "agentx.kernel.risk",
    "agentx.kernel.secrets",
    "agentx.learning",
    "agentx.models",
    "agentx.procedures",
    "agentx.research",
)

#: Canonical contracts that must stay imported, never re-declared here.
_FORBIDDEN_DEFINITIONS = frozenset(
    {
        "AgentLoop",
        "Capability",
        "CapabilityExecutionLoop",
        "CapabilityRegistry",
        "EmergencyStop",
        "ExecutionLevel",
        "ExecutionLevelEscalator",
        "ExecutionLevelRouter",
        "Executor",
        "KnowledgeGapDetector",
        "LoopGuard",
        "LoopGuardLimits",
        "Reasoner",
        "ResearchAcquisitionPort",
        "ResearchObjective",
        "ResearchRequest",
        "ResearchResponse",
        "ResourceBudget",
        "StrategyRegistry",
        "StrategyResult",
        "Verifier",
    }
)

#: Names that would mean the boundary mutates a kernel control plane.
_FORBIDDEN_CALLED_ATTRIBUTES = frozenset(
    {
        "activate",
        "approve",
        "assign",
        "cancel",
        "check_and_consume",
        "clear",
        "consume",
        "decide",
        "escalate",
        "evaluate_envelope",
        "execute",
        "grant",
        "publish",
        "remember",
        "request_stop",
        "reset",
        "route",
        "save",
        "store",
        "transition",
        "verify",
        "write",
    }
)

_FORBIDDEN_SOURCE_MARKERS = (
    "eval(",
    "exec(",
    "compile(",
    "__import__",
    "open(",
    "Path(",
    "os.system",
    "subprocess",
    "Popen",
    "socket",
    "urlopen",
    "requests.",
    "http.",
    "WinDLL",
    "windll",
    "SetWindowsHookEx",
    "SendInput",
    "Thread(",
    "Process(",
    "asyncio",
    "time.sleep",
    "datetime.now",
    "uuid4",
    "random.",
    "sqlite3",
    "ClosedLoopOutcome",
    "StrategyResult.executed",
    "Permission(",
    "RiskAssessment(",
    "AuthorityContext(",
    "KnowledgeStatus.VERIFIED",
    "ProcedureStatus.ACTIVE",
    "TaskStatus.SUCCEEDED",
)


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _code_only(path: Path) -> str:
    """Return the module text with comments and docstring lines removed.

    The boundary explains its own non-scope at length, so prose that *names*
    shell execution or a socket is expected and harmless. The architecture claim
    is about code, so marker checks run against code with every docstring and
    comment stripped out.
    """
    source = path.read_text(encoding="utf-8")
    lines = source.splitlines()
    tree = ast.parse(source)
    stripped: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = getattr(node, "body", ())
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                start, end = body[0].lineno, body[0].end_lineno
                stripped.update(range(start, (end if end is not None else start) + 1))
    kept = []
    for number, line in enumerate(lines, start=1):
        if number in stripped or line.lstrip().startswith("#"):
            continue
        kept.append(line.split("#", 1)[0] if "#" in line else line)
    return "\n".join(kept)


def _imports(path: Path) -> tuple[str, ...]:
    imported: list[str] = []
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.append(node.module)
    return tuple(dict.fromkeys(imported))


def _defined_classes(path: Path) -> set[str]:
    return {node.name for node in ast.walk(_tree(path)) if isinstance(node, ast.ClassDef)}


def _class_body(name: str) -> ast.ClassDef:
    for node in _tree(_BOUNDARY).body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise AssertionError(f"{name} is not defined at module level")


def _field_names(class_name: str) -> set[str]:
    return {
        target.id
        for node in _class_body(class_name).body
        if isinstance(node, ast.AnnAssign)
        for target in [node.target]
        if isinstance(target, ast.Name)
    }


def _bound_self_attribute(attr: str) -> set[str]:
    """Return the ``self._x`` names whose attribute ``attr`` is used."""
    owners: set[str] = set()
    for node in ast.walk(_tree(_BOUNDARY)):
        if isinstance(node, ast.Attribute) and node.attr == attr:
            value = node.value
            if (
                isinstance(value, ast.Attribute)
                and isinstance(value.value, ast.Name)
                and value.value.id == "self"
            ):
                owners.add(value.attr)
    return owners


# ---------------------------------------------------------------------------
# Placement: one top-level composition module, no new subsystem.
# ---------------------------------------------------------------------------


def test_boundary_is_one_top_level_composition_module() -> None:
    assert _BOUNDARY.is_file()
    assert _BOUNDARY.parent == _AGENTX_ROOT
    assert not (_AGENTX_ROOT / "exploratory_strategy").exists()
    assert not (_AGENTX_ROOT / "research").exists()
    assert not (_AGENTX_ROOT / "exploration").exists()


def test_no_subsystem_imports_the_composition_boundary() -> None:
    for path in sorted(_AGENTX_ROOT.rglob("*.py")):
        if path in (_BOUNDARY, _ARCHITECTURE):
            continue
        assert "agentx.exploratory_strategy" not in _imports(path), path
        assert "L5ExploratoryStrategy" not in path.read_text(encoding="utf-8"), path


def test_agent_loop_and_capability_strategy_are_untouched() -> None:
    """Nothing in the composition root or the sibling adapter knows this exists.

    ``L5_EXPLORATORY`` itself is canonical router vocabulary and legitimately
    appears in ``agent_loop`` prose; what must stay absent is any reference to
    this boundary's module or classes.
    """
    for path in (_AGENT_LOOP, _CAPABILITY_STRATEGY, _PACKAGE_INIT):
        source = path.read_text(encoding="utf-8")
        assert "exploratory_strategy" not in source, path
        assert "L5ExploratoryStrategy" not in source, path
        assert "ExploratoryResearch" not in source, path


def test_architecture_manifest_has_no_boundary_special_case_or_new_edge() -> None:
    source = _ARCHITECTURE.read_text(encoding="utf-8")
    assert "exploratory" not in source.lower()
    assert (
        _architecture.COGNITION,
        _architecture.CAPABILITIES,
    ) not in _architecture.ALLOWED_ARCHITECTURE_EDGES


# ---------------------------------------------------------------------------
# Import surface.
# ---------------------------------------------------------------------------


def test_boundary_imports_only_exact_canonical_composition_contracts() -> None:
    imports = _imports(_BOUNDARY)
    agentx_imports = {module for module in imports if module.startswith("agentx")}
    assert agentx_imports == _ALLOWED_AGENTX_IMPORTS
    for module in imports:
        root = module.split(".")[0]
        assert root in _ALLOWED_STDLIB_IMPORTS or root == "agentx", module
        assert root not in _FORBIDDEN_IMPORT_ROOTS, module
        for prefix in _FORBIDDEN_AGENTX_PREFIXES:
            assert module != prefix and not module.startswith(f"{prefix}."), module


def test_third_party_and_runtime_dependency_surface_is_empty() -> None:
    third_party = {
        module.split(".")[0]
        for module in _imports(_BOUNDARY)
        if not module.startswith("agentx") and module not in _ALLOWED_STDLIB_IMPORTS
    }
    assert third_party == set()
    pyproject = _PYPROJECT.read_text(encoding="utf-8")
    assert "dependencies = []" in pyproject


# ---------------------------------------------------------------------------
# No execution, acquisition, or mutation surface of its own.
# ---------------------------------------------------------------------------


def test_boundary_has_no_dynamic_execution_or_filesystem_surface() -> None:
    called_names = {
        node.func.id
        for node in ast.walk(_tree(_BOUNDARY))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert not called_names & {"eval", "exec", "compile", "__import__", "open", "input"}
    code = _code_only(_BOUNDARY)
    for marker in _FORBIDDEN_SOURCE_MARKERS:
        assert marker not in code, marker


def test_only_acquire_is_called_on_the_injected_research_port() -> None:
    """The one outward interaction is ``acquire``, reached through one local read."""
    port_attributes = {
        node.attr
        for node in ast.walk(_tree(_BOUNDARY))
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "port"
    }
    assert port_attributes == {"acquire"}
    # ``self._port`` itself is only read once, into that local; nothing else is
    # ever called on it, and it is never written.
    assert _bound_self_attribute("acquire") == set()
    assert "port = self._port" in _code_only(_BOUNDARY)


def test_injected_kernel_objects_are_only_read() -> None:
    assert _bound_self_attribute("snapshot") == {"_budget"}
    assert _bound_self_attribute("stop_requested") == {"_emergency_stop"}
    assert _bound_self_attribute("reason") == {"_reasoner"}
    assert _bound_self_attribute("evaluate") == {"_loop_guard"}
    used = {
        node.attr
        for node in ast.walk(_tree(_BOUNDARY))
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Attribute)
        and isinstance(node.value.value, ast.Name)
        and node.value.value.id == "self"
        and node.value.attr in {"_budget", "_emergency_stop", "_port", "_reasoner"}
    }
    # ``envelope`` is read only to learn the canonical research-query ceiling;
    # the loop guard and the port are reached through single local reads.
    assert used == {"envelope", "reason", "snapshot", "stop_requested"}
    assert not used & _FORBIDDEN_CALLED_ATTRIBUTES


def test_no_kernel_control_attribute_is_called_anywhere() -> None:
    called = {
        node.func.attr
        for node in ast.walk(_tree(_BOUNDARY))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert not called & _FORBIDDEN_CALLED_ATTRIBUTES, called & _FORBIDDEN_CALLED_ATTRIBUTES


def test_no_state_is_assigned_onto_injected_collaborators() -> None:
    for node in ast.walk(_tree(_BOUNDARY)):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Attribute):
                assert not (
                    isinstance(target.value.value, ast.Name)
                    and target.value.value.id == "self"
                    and target.value.attr
                    in {"_budget", "_emergency_stop", "_port", "_reasoner", "_loop_guard"}
                ), target.value.attr


# ---------------------------------------------------------------------------
# No duplicated canonical abstraction.
# ---------------------------------------------------------------------------


def test_boundary_defines_only_its_own_exploratory_vocabulary() -> None:
    assert _defined_classes(_BOUNDARY) == {
        "ExploratoryStrategyError",
        "ExplorationLimits",
        "ResearchEvidence",
        "ExploratoryStepDisposition",
        "ExploratoryStatus",
        "ExploratoryStep",
        "ExploratoryResearchRequest",
        "ExploratoryResearchResult",
        "L5ExploratoryStrategy",
    }
    assert not _defined_classes(_BOUNDARY) & _FORBIDDEN_DEFINITIONS


def test_anti_loop_and_budget_are_reused_not_reimplemented() -> None:
    limits_fields = _field_names("ExplorationLimits")
    assert limits_fields == {"max_research_steps", "loop_guard_limits"}
    # The loop guard is the canonical evaluator, constructed as such, and the
    # limits it receives are the canonical A2.09 value type: no second table.
    code = _code_only(_BOUNDARY)
    assert "LoopGuard()" in code
    assert "class LoopGuard" not in code
    assert "class ResourceBudget" not in code
    assert "LoopGuardLimits" in ast.unparse(_class_body("ExplorationLimits"))


def test_no_new_dependency_or_import_hook_is_registered() -> None:
    code = _code_only(_BOUNDARY)
    for marker in ("importlib", "sys.path", "__all__ = ["):
        if marker == "__all__ = [":
            continue
        assert marker not in code, marker


def test_exploratory_status_carries_no_authority_vocabulary() -> None:
    members = {
        target.id
        for node in _class_body("ExploratoryStatus").body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    assert members == {
        "EVIDENCE_COLLECTED",
        "NO_EVIDENCE",
        "CONTEXT_SUFFICIENT",
        "PORT_UNBOUND",
        "STEP_CEILING_REACHED",
        "BUDGET_EXHAUSTED",
        "ANTI_LOOP_STOPPED",
        "STOP_OBSERVED",
        "EMERGENCY_STOPPED",
        "MALFORMED_RESPONSE",
    }
    for forbidden in ("SUCCESS", "VERIFIED", "ALLOWED", "GRANTED", "ACTIVATED", "PROMOTED"):
        assert not any(forbidden in name for name in members), forbidden


def test_evidence_value_carries_no_authority_vocabulary() -> None:
    assert _field_names("ResearchEvidence") == {
        "step_index",
        "objective_id",
        "request_id",
        "provider_id",
        "reference",
    }
    methods = {
        node.name
        for node in _class_body("ResearchEvidence").body
        if isinstance(node, ast.FunctionDef)
    }
    assert methods == {"__post_init__", "kind"}


def test_only_the_exact_level_is_declared() -> None:
    assignments = {
        target.id: node
        for node in _tree(_BOUNDARY).body
        if isinstance(node, ast.AnnAssign)
        for target in [node.target]
        if isinstance(target, ast.Name)
    }
    declaration = assignments["EXPLORATORY_STRATEGY_LEVEL"]
    assert isinstance(declaration.value, ast.Attribute)
    assert isinstance(declaration.value.value, ast.Name)
    assert declaration.value.value.id == "ExecutionLevel"
    assert declaration.value.attr == "L5_EXPLORATORY"


def test_no_module_singleton_or_hidden_default_limits_exist() -> None:
    module_level_assignments = [
        node for node in _tree(_BOUNDARY).body if isinstance(node, ast.Assign | ast.AnnAssign)
    ]
    for node in module_level_assignments:
        value = node.value
        if value is None:
            continue
        names = {seen.id for seen in ast.walk(value) if isinstance(seen, ast.Name)}
        # Import time builds constants only: no collaborator, no instance, and
        # no class defined in this module is constructed at module level.
        assert names <= {"ExecutionLevel", "frozenset", "string"}, names
    names = {
        target.id
        for node in module_level_assignments
        if isinstance(node, ast.AnnAssign)
        for target in [node.target]
        if isinstance(target, ast.Name)
    }
    assert names == {
        "EXPLORATORY_STRATEGY_LEVEL",
        "MAX_EXPLORATORY_RESEARCH_STEPS",
        "OBJECTIVE_WIDE_TARGET",
        "_ERROR_PREFIX",
        "_MAX_TOKEN_LENGTH",
        "_TOKEN_DIGEST_LENGTH",
        "_TOKEN_SAFE_CHARACTERS",
    }
    assert "DEFAULT_" not in _code_only(_BOUNDARY)


# ---------------------------------------------------------------------------
# The single A2.10 report, and the documentation of the boundary.
# ---------------------------------------------------------------------------


def test_attempt_can_only_report_unavailability() -> None:
    code = _code_only(_BOUNDARY)
    assert code.count("StrategyResult.unavailable(") == 3
    assert "StrategyResult.executed" not in code
    assert "ClosedLoopOutcome" not in code


def test_boundary_is_documented_as_data_not_authority() -> None:
    assert _DOC.is_file()
    text = _DOC.read_text(encoding="utf-8").lower()
    for required in (
        "l5_exploratory",
        "untrusted",
        "browser",
        "scraping",
        "subprocess",
        "eval",
        "verification",
        "procedure",
        "resource budget",
        "emergency stop",
        "zero runtime dependencies",
    ):
        assert required in text, required
