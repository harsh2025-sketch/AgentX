"""Lightweight specialized models and reproducible M14 benchmark support.

The specialists in this module are bounded advisory classifiers. They never
replace canonical AgentX verification, grant authority, execute capabilities,
or deserialize executable objects. Artifacts are deterministic JSON-compatible
data only.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Final
from uuid import UUID

from agentx.cognition.router import ExecutionLevel
from agentx.core.reuse_efficiency import ExecutionEvidenceOutcome
from agentx.strategy_performance_evidence import StrategyPerformanceEvidence

__all__ = [
    "DistillationArtifact",
    "DistillationPipeline",
    "GroundingExample",
    "GroundingLabel",
    "GroundingSpecialistDataset",
    "LightweightGroundingModel",
    "LightweightRoutingModel",
    "RoutingExample",
    "RoutingSpecialistDataset",
    "SpecialistBenchmark",
    "SpecialistBenchmarkReport",
    "SpecialistModelError",
    "VerifierAssistanceExperiment",
    "VerifierAssistanceReport",
]

_MAX_TEXT: Final[int] = 1024
_MODEL_SCHEMA: Final[int] = 1


class SpecialistModelError(ValueError):
    """Raised for invalid specialist datasets, models, and benchmark evidence."""


def _text(value: object, *, name: str, max_length: int = _MAX_TEXT) -> str:
    if not isinstance(value, str):
        raise SpecialistModelError(f"{name} must be a string")
    if not value or value != value.strip():
        raise SpecialistModelError(f"{name} must be non-empty and trimmed")
    if len(value) > max_length:
        raise SpecialistModelError(f"{name} exceeds {max_length} characters")
    if "\x00" in value:
        raise SpecialistModelError(f"{name} contains NUL")
    return value


def _tokens(text: str) -> tuple[str, ...]:
    normalized = "".join(
        character.lower() if character.isalnum() else " "
        for character in text
    )
    return tuple(token for token in normalized.split() if token)[:256]


def _dataset_id(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


class GroundingLabel(StrEnum):
    GROUNDED = "grounded"
    NOT_GROUNDED = "not_grounded"


@dataclass(frozen=True, slots=True, kw_only=True)
class GroundingExample:
    """One externally verified grounding label with explicit provenance."""

    example_id: UUID
    claim: str
    evidence_text: str
    label: GroundingLabel
    provenance_source: str
    provenance_reference: str

    def __post_init__(self) -> None:
        if not isinstance(self.example_id, UUID) or self.example_id.int == 0:
            raise SpecialistModelError("example_id must be a non-nil UUID")
        object.__setattr__(self, "claim", _text(self.claim, name="claim"))
        object.__setattr__(
            self,
            "evidence_text",
            _text(self.evidence_text, name="evidence_text", max_length=4096),
        )
        if not isinstance(self.label, GroundingLabel):
            raise SpecialistModelError("label must be GroundingLabel")
        object.__setattr__(
            self,
            "provenance_source",
            _text(self.provenance_source, name="provenance_source", max_length=256),
        )
        object.__setattr__(
            self,
            "provenance_reference",
            _text(
                self.provenance_reference,
                name="provenance_reference",
                max_length=512,
            ),
        )


@dataclass(frozen=True, slots=True)
class GroundingSpecialistDataset:
    examples: tuple[GroundingExample, ...]
    dataset_id: str

    @classmethod
    def build(
        cls,
        examples: Sequence[GroundingExample],
    ) -> GroundingSpecialistDataset:
        ordered = tuple(sorted(examples, key=lambda item: str(item.example_id)))
        if len({item.example_id for item in ordered}) != len(ordered):
            raise SpecialistModelError("grounding dataset contains duplicate example ids")
        payload = [
            {
                "example_id": str(item.example_id),
                "claim": item.claim,
                "evidence_text": item.evidence_text,
                "label": item.label.value,
                "provenance_source": item.provenance_source,
                "provenance_reference": item.provenance_reference,
            }
            for item in ordered
        ]
        return cls(examples=ordered, dataset_id=_dataset_id(payload))


@dataclass(frozen=True, slots=True)
class _BinaryTokenModel:
    dataset_id: str
    positive: tuple[tuple[str, int], ...]
    negative: tuple[tuple[str, int], ...]
    positive_examples: int
    negative_examples: int
    schema_version: int = _MODEL_SCHEMA

    def __post_init__(self) -> None:
        _text(self.dataset_id, name="dataset_id", max_length=128)
        if type(self.positive_examples) is not int or self.positive_examples < 0:
            raise SpecialistModelError("positive_examples must be non-negative integer")
        if type(self.negative_examples) is not int or self.negative_examples < 0:
            raise SpecialistModelError("negative_examples must be non-negative integer")
        if self.schema_version != _MODEL_SCHEMA:
            raise SpecialistModelError("unsupported specialist model schema")

    def score(self, text: str) -> tuple[int, int]:
        counts = Counter(_tokens(text))
        positive_map = dict(self.positive)
        negative_map = dict(self.negative)
        positive_score = sum(
            counts[token] * positive_map.get(token, 0) for token in counts
        )
        negative_score = sum(
            counts[token] * negative_map.get(token, 0) for token in counts
        )
        return positive_score, negative_score

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "dataset_id": self.dataset_id,
            "positive": [[token, count] for token, count in self.positive],
            "negative": [[token, count] for token, count in self.negative],
            "positive_examples": self.positive_examples,
            "negative_examples": self.negative_examples,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> _BinaryTokenModel:
        expected = {
            "schema_version",
            "dataset_id",
            "positive",
            "negative",
            "positive_examples",
            "negative_examples",
        }
        if set(raw) != expected:
            raise SpecialistModelError("model artifact has missing or unknown fields")
        positive = raw["positive"]
        negative = raw["negative"]
        if not isinstance(positive, list) or not isinstance(negative, list):
            raise SpecialistModelError("model token tables must be arrays")

        def parse_table(value: list[object]) -> tuple[tuple[str, int], ...]:
            result: list[tuple[str, int]] = []
            for item in value:
                if (
                    not isinstance(item, list)
                    or len(item) != 2
                    or not isinstance(item[0], str)
                    or type(item[1]) is not int
                    or item[1] < 0
                ):
                    raise SpecialistModelError("invalid model token table entry")
                result.append((item[0], item[1]))
            return tuple(result)

        dataset_id = raw["dataset_id"]
        if not isinstance(dataset_id, str):
            raise SpecialistModelError("dataset_id must be string")
        schema_version = raw["schema_version"]
        positive_examples = raw["positive_examples"]
        negative_examples = raw["negative_examples"]
        if type(schema_version) is not int:
            raise SpecialistModelError("schema_version must be integer")
        if type(positive_examples) is not int or type(negative_examples) is not int:
            raise SpecialistModelError("example counts must be integers")
        return cls(
            schema_version=schema_version,
            dataset_id=dataset_id,
            positive=parse_table(positive),
            negative=parse_table(negative),
            positive_examples=positive_examples,
            negative_examples=negative_examples,
        )


@dataclass(frozen=True, slots=True)
class GroundingPrediction:
    label: GroundingLabel | None
    confidence: Decimal
    advisory_only: bool = True


@dataclass(frozen=True, slots=True)
class LightweightGroundingModel:
    model_id: str
    token_model: _BinaryTokenModel

    @classmethod
    def train(
        cls,
        dataset: GroundingSpecialistDataset,
    ) -> LightweightGroundingModel:
        if not isinstance(dataset, GroundingSpecialistDataset):
            raise SpecialistModelError("dataset must be GroundingSpecialistDataset")
        positive: Counter[str] = Counter()
        negative: Counter[str] = Counter()
        positive_examples = 0
        negative_examples = 0
        for item in dataset.examples:
            bag = _tokens(f"{item.claim} {item.evidence_text}")
            if item.label is GroundingLabel.GROUNDED:
                positive.update(bag)
                positive_examples += 1
            else:
                negative.update(bag)
                negative_examples += 1
        token_model = _BinaryTokenModel(
            dataset_id=dataset.dataset_id,
            positive=tuple(sorted(positive.items())),
            negative=tuple(sorted(negative.items())),
            positive_examples=positive_examples,
            negative_examples=negative_examples,
        )
        model_id = _dataset_id(token_model.to_dict())
        return cls(model_id=model_id, token_model=token_model)

    def predict(
        self,
        *,
        claim: str,
        evidence_text: str,
        minimum_margin: int = 1,
    ) -> GroundingPrediction:
        if type(minimum_margin) is not int or minimum_margin < 0:
            raise SpecialistModelError("minimum_margin must be non-negative integer")
        text = f"{_text(claim, name='claim')} {_text(evidence_text, name='evidence_text', max_length=4096)}"
        positive, negative = self.token_model.score(text)
        total = positive + negative
        if total == 0 or abs(positive - negative) < minimum_margin:
            return GroundingPrediction(label=None, confidence=Decimal(0))
        label = (
            GroundingLabel.GROUNDED
            if positive > negative
            else GroundingLabel.NOT_GROUNDED
        )
        confidence = Decimal(abs(positive - negative)) / Decimal(total)
        return GroundingPrediction(label=label, confidence=confidence)

    def to_json(self) -> str:
        return json.dumps(
            {"model_id": self.model_id, "token_model": self.token_model.to_dict()},
            separators=(",", ":"),
            sort_keys=True,
        )

    @classmethod
    def from_json(cls, value: str) -> LightweightGroundingModel:
        if not isinstance(value, str):
            raise SpecialistModelError("model artifact must be JSON string")
        try:
            raw = json.loads(value)
        except ValueError as exc:
            raise SpecialistModelError("model artifact JSON is malformed") from exc
        if not isinstance(raw, Mapping) or set(raw) != {"model_id", "token_model"}:
            raise SpecialistModelError("model artifact shape is invalid")
        model_id = raw["model_id"]
        token_model = raw["token_model"]
        if not isinstance(model_id, str) or not isinstance(token_model, Mapping):
            raise SpecialistModelError("model artifact fields are invalid")
        model = cls(
            model_id=model_id,
            token_model=_BinaryTokenModel.from_dict(token_model),
        )
        if _dataset_id(model.token_model.to_dict()) != model.model_id:
            raise SpecialistModelError("model artifact identity mismatch")
        return model


@dataclass(frozen=True, slots=True, kw_only=True)
class RoutingExample:
    evidence_id: UUID
    task_family: str
    context_text: str
    strategy: ExecutionLevel
    verified_success: bool
    provenance_reference: str

    def __post_init__(self) -> None:
        if not isinstance(self.evidence_id, UUID) or self.evidence_id.int == 0:
            raise SpecialistModelError("evidence_id must be a non-nil UUID")
        object.__setattr__(
            self,
            "task_family",
            _text(self.task_family, name="task_family", max_length=256),
        )
        object.__setattr__(
            self,
            "context_text",
            _text(self.context_text, name="context_text", max_length=2048),
        )
        if not isinstance(self.strategy, ExecutionLevel):
            raise SpecialistModelError("strategy must be ExecutionLevel")
        if not isinstance(self.verified_success, bool):
            raise SpecialistModelError("verified_success must be bool")
        object.__setattr__(
            self,
            "provenance_reference",
            _text(
                self.provenance_reference,
                name="provenance_reference",
                max_length=512,
            ),
        )

    @classmethod
    def from_performance(
        cls,
        performance: StrategyPerformanceEvidence,
        *,
        task_family: str,
        context_text: str,
        provenance_reference: str,
    ) -> RoutingExample:
        if performance.outcome is ExecutionEvidenceOutcome.UNVERIFIED:
            raise SpecialistModelError(
                "routing training example requires a verified success or failure"
            )
        verified_success = (
            performance.outcome is ExecutionEvidenceOutcome.VERIFIED_SUCCESS
            and performance.verification_passed is True
        )
        return cls(
            evidence_id=performance.evidence_id,
            task_family=task_family,
            context_text=context_text,
            strategy=performance.execution_level,
            verified_success=verified_success,
            provenance_reference=provenance_reference,
        )


@dataclass(frozen=True, slots=True)
class RoutingSpecialistDataset:
    examples: tuple[RoutingExample, ...]
    dataset_id: str

    @classmethod
    def build(
        cls,
        examples: Sequence[RoutingExample],
    ) -> RoutingSpecialistDataset:
        ordered = tuple(sorted(examples, key=lambda item: str(item.evidence_id)))
        if len({item.evidence_id for item in ordered}) != len(ordered):
            raise SpecialistModelError("routing dataset contains duplicate evidence ids")
        payload = [
            {
                "evidence_id": str(item.evidence_id),
                "task_family": item.task_family,
                "context_text": item.context_text,
                "strategy": item.strategy.value,
                "verified_success": item.verified_success,
                "provenance_reference": item.provenance_reference,
            }
            for item in ordered
        ]
        return cls(examples=ordered, dataset_id=_dataset_id(payload))


@dataclass(frozen=True, slots=True)
class RoutingPrediction:
    level: ExecutionLevel | None
    confidence: Decimal


@dataclass(frozen=True, slots=True)
class LightweightRoutingModel:
    """Success-only token/family counts with explicit low-confidence fallback."""

    model_id: str
    dataset_id: str
    family_counts: tuple[tuple[str, ExecutionLevel, int], ...]
    token_counts: tuple[tuple[str, ExecutionLevel, int], ...]

    @classmethod
    def train(
        cls,
        dataset: RoutingSpecialistDataset,
    ) -> LightweightRoutingModel:
        if not isinstance(dataset, RoutingSpecialistDataset):
            raise SpecialistModelError("dataset must be RoutingSpecialistDataset")
        family: dict[tuple[str, ExecutionLevel], int] = defaultdict(int)
        token: dict[tuple[str, ExecutionLevel], int] = defaultdict(int)
        for item in dataset.examples:
            if not item.verified_success:
                continue
            family[(item.task_family, item.strategy)] += 1
            for word in _tokens(item.context_text):
                token[(word, item.strategy)] += 1
        raw = {
            "dataset_id": dataset.dataset_id,
            "family_counts": [
                [family_name, level.value, count]
                for (family_name, level), count in sorted(
                    family.items(), key=lambda item: (item[0][0], item[0][1].value)
                )
            ],
            "token_counts": [
                [word, level.value, count]
                for (word, level), count in sorted(
                    token.items(), key=lambda item: (item[0][0], item[0][1].value)
                )
            ],
        }
        return cls(
            model_id=_dataset_id(raw),
            dataset_id=dataset.dataset_id,
            family_counts=tuple(
                (family_name, level, count)
                for (family_name, level), count in sorted(
                    family.items(), key=lambda item: (item[0][0], item[0][1].value)
                )
            ),
            token_counts=tuple(
                (word, level, count)
                for (word, level), count in sorted(
                    token.items(), key=lambda item: (item[0][0], item[0][1].value)
                )
            ),
        )

    def predict(
        self,
        *,
        task_family: str,
        context_text: str,
        available: tuple[ExecutionLevel, ...],
        minimum_score: int = 1,
    ) -> RoutingPrediction:
        task_family = _text(task_family, name="task_family", max_length=256)
        context_text = _text(context_text, name="context_text", max_length=2048)
        if not available or any(not isinstance(level, ExecutionLevel) for level in available):
            raise SpecialistModelError(
                "available must be non-empty ExecutionLevel tuple"
            )
        if type(minimum_score) is not int or minimum_score < 1:
            raise SpecialistModelError("minimum_score must be positive integer")
        scores = {level: 0 for level in available}
        for family_name, level, count in self.family_counts:
            if family_name == task_family and level in scores:
                scores[level] += count * 2
        words = Counter(_tokens(context_text))
        for word, level, count in self.token_counts:
            if level in scores:
                scores[level] += words[word] * count
        best_score = max(scores.values())
        if best_score < minimum_score:
            return RoutingPrediction(level=None, confidence=Decimal(0))
        best = tuple(level for level in available if scores[level] == best_score)
        if len(best) != 1:
            return RoutingPrediction(level=None, confidence=Decimal(0))
        total = sum(scores.values())
        return RoutingPrediction(
            level=best[0],
            confidence=(
                Decimal(1) if total == 0 else Decimal(best_score) / Decimal(total)
            ),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class VerifierAssistanceReport:
    samples: int
    true_positive: int
    false_positive: int
    true_negative: int
    false_negative: int
    precision: Decimal | None
    recall: Decimal | None
    sole_authority: bool = False


@dataclass(frozen=True, slots=True)
class VerifierAssistanceExperiment:
    """Measures advisory grounding output against canonical verification truth."""

    def run(
        self,
        predictions: Sequence[GroundingPrediction],
        truths: Sequence[bool],
    ) -> VerifierAssistanceReport:
        if len(predictions) != len(truths):
            raise SpecialistModelError("predictions and truths length mismatch")
        tp = fp = tn = fn = 0
        for prediction, truth in zip(predictions, truths, strict=True):
            if not isinstance(prediction, GroundingPrediction) or not isinstance(truth, bool):
                raise SpecialistModelError("invalid verifier-assistance sample")
            predicted = prediction.label is GroundingLabel.GROUNDED
            if prediction.label is None:
                predicted = False
            if predicted and truth:
                tp += 1
            elif predicted and not truth:
                fp += 1
            elif not predicted and not truth:
                tn += 1
            else:
                fn += 1
        precision = None if tp + fp == 0 else Decimal(tp) / Decimal(tp + fp)
        recall = None if tp + fn == 0 else Decimal(tp) / Decimal(tp + fn)
        return VerifierAssistanceReport(
            samples=len(truths),
            true_positive=tp,
            false_positive=fp,
            true_negative=tn,
            false_negative=fn,
            precision=precision,
            recall=recall,
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class DistillationArtifact:
    teacher_id: str
    dataset_id: str
    student_model_id: str
    training_config_id: str
    evaluation_reference: str

    def __post_init__(self) -> None:
        for name in (
            "teacher_id",
            "dataset_id",
            "student_model_id",
            "training_config_id",
            "evaluation_reference",
        ):
            object.__setattr__(
                self,
                name,
                _text(getattr(self, name), name=name, max_length=512),
            )


@dataclass(frozen=True, slots=True)
class DistillationPipeline:
    """Records a reproducible candidate lineage; it never promotes the candidate."""

    def distill_grounding(
        self,
        *,
        teacher_id: str,
        dataset: GroundingSpecialistDataset,
        training_config: Mapping[str, object],
        evaluation_reference: str,
    ) -> tuple[LightweightGroundingModel, DistillationArtifact]:
        if not isinstance(training_config, Mapping):
            raise SpecialistModelError("training_config must be mapping")
        try:
            config_json = json.dumps(
                dict(training_config),
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        except (TypeError, ValueError) as exc:
            raise SpecialistModelError(
                "training_config must be JSON-compatible"
            ) from exc
        config_id = _dataset_id(json.loads(config_json))
        model = LightweightGroundingModel.train(dataset)
        return model, DistillationArtifact(
            teacher_id=teacher_id,
            dataset_id=dataset.dataset_id,
            student_model_id=model.model_id,
            training_config_id=config_id,
            evaluation_reference=evaluation_reference,
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class SpecialistBenchmarkReport:
    name: str
    samples: int
    baseline_correct: int
    specialist_correct: int
    baseline_accuracy: Decimal | None
    specialist_accuracy: Decimal | None
    disposition: str


@dataclass(frozen=True, slots=True)
class SpecialistBenchmark:
    """Truthful exact-count benchmark; no significance claim is inferred."""

    def compare_labels(
        self,
        *,
        name: str,
        baseline: Sequence[object],
        specialist: Sequence[object],
        truth: Sequence[object],
    ) -> SpecialistBenchmarkReport:
        name = _text(name, name="name", max_length=128)
        if len(baseline) != len(specialist) or len(truth) != len(specialist):
            raise SpecialistModelError("benchmark vectors must have equal lengths")
        samples = len(truth)
        baseline_correct = sum(
            predicted == expected
            for predicted, expected in zip(baseline, truth, strict=True)
        )
        specialist_correct = sum(
            predicted == expected
            for predicted, expected in zip(specialist, truth, strict=True)
        )
        baseline_accuracy = (
            None if samples == 0 else Decimal(baseline_correct) / Decimal(samples)
        )
        specialist_accuracy = (
            None if samples == 0 else Decimal(specialist_correct) / Decimal(samples)
        )
        if samples == 0:
            disposition = "insufficient_evidence"
        elif specialist_correct > baseline_correct:
            disposition = "improved"
        elif specialist_correct < baseline_correct:
            disposition = "regressed"
        else:
            disposition = "same"
        return SpecialistBenchmarkReport(
            name=name,
            samples=samples,
            baseline_correct=baseline_correct,
            specialist_correct=specialist_correct,
            baseline_accuracy=baseline_accuracy,
            specialist_accuracy=specialist_accuracy,
            disposition=disposition,
        )
