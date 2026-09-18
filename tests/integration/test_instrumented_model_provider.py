"""Metrics integration over the concrete HTTP provider, using loopback transport."""

from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from agentx.cognition.http_model_provider import HttpModelProvider
from agentx.cognition.model_provider import ModelRequest, ModelResponse, ProviderDescriptor
from agentx.cognition.router import ExecutionLevel
from agentx.core.errors import AgentXError
from agentx.core.ids import TaskId
from agentx.core.result import Result
from agentx.execution_metrics import ExecutionMetricsRecorder, ExecutionMetricsValidationError
from agentx.instrumented_model_provider import InstrumentedModelProvider
from tests.integration.test_http_model_provider import completion, request, server


def recorder(*, start: bool = True) -> ExecutionMetricsRecorder:
    result = ExecutionMetricsRecorder(
        task_id=TaskId.create(),
        correlation_id=uuid4(),
        execution_level=ExecutionLevel.L4_PLANNED,
        cost_unit="USD",
    )
    if start:
        result.start(started_at=datetime.now(UTC))
    return result


class RaisingProvider:
    def __init__(self, descriptor: ProviderDescriptor) -> None:
        self.descriptor = descriptor

    def invoke(self, request: ModelRequest) -> Result[ModelResponse, AgentXError]:
        raise RuntimeError("private-provider-exception")


class InstrumentedModelProviderTests(unittest.TestCase):
    def test_real_http_usage_reaches_canonical_metrics(self) -> None:
        with server() as (config, scenario):
            metrics = recorder()
            concrete = HttpModelProvider(config)
            provider = InstrumentedModelProvider(provider=concrete, recorder=metrics)
            self.assertIs(provider.descriptor, concrete.descriptor)
            response = provider.invoke(request()).unwrap()
            record = metrics.finish(elapsed=timedelta(seconds=1))
            self.assertEqual(len(scenario.requests), 1)
            self.assertEqual(record.model_calls, 1)
            self.assertEqual(record.model_input_tokens, 4)
            self.assertEqual(record.model_output_tokens, 5)
            self.assertEqual(record.model_tokens, response.usage.accounted_tokens)
            self.assertIsNone(record.external_cost)
            self.assertFalse(record.verified_success)
            self.assertNotIn(response.content[0].text, repr(record))

    def test_failed_http_call_counts_without_inventing_tokens_or_cost(self) -> None:
        with server() as (config, scenario):
            metrics = recorder()
            scenario.status = 503
            provider = InstrumentedModelProvider(
                provider=HttpModelProvider(config), recorder=metrics
            )
            result = provider.invoke(request())
            self.assertEqual(result.unwrap_error().code, "model_provider.unavailable")
            record = metrics.finish(elapsed=timedelta(seconds=1))
            self.assertEqual(len(scenario.requests), 1)
            self.assertEqual(record.model_calls, 1)
            self.assertIsNone(record.model_tokens)
            self.assertIsNone(record.external_cost)

    def test_missing_usage_invalidates_run_total_instead_of_retaining_subtotal(self) -> None:
        with server() as (config, scenario):
            metrics = recorder()
            provider = InstrumentedModelProvider(
                provider=HttpModelProvider(config), recorder=metrics
            )
            provider.invoke(request()).unwrap()
            missing = completion()
            del missing["usage"]
            scenario.response_body = json.dumps(missing).encode()
            provider.invoke(request()).unwrap()
            record = metrics.finish(elapsed=timedelta(seconds=1))
            self.assertEqual(record.model_calls, 2)
            self.assertIsNone(record.model_tokens)
            self.assertIsNone(record.model_input_tokens)
            self.assertIsNone(record.model_output_tokens)

    def test_closed_or_unstarted_session_performs_no_http_request(self) -> None:
        with server() as (config, scenario):
            for started in (False, True):
                metrics = recorder(start=started)
                if started:
                    metrics.finish(elapsed=timedelta(0))
                provider = InstrumentedModelProvider(
                    provider=HttpModelProvider(config), recorder=metrics
                )
                with self.assertRaises(ExecutionMetricsValidationError):
                    provider.invoke(request())
            self.assertEqual(scenario.requests, [])

    def test_provider_exception_counts_once_and_its_text_is_not_metrics(self) -> None:
        with server() as (config, _scenario):
            metrics = recorder()
            provider = InstrumentedModelProvider(
                provider=RaisingProvider(HttpModelProvider(config).descriptor), recorder=metrics
            )
            with self.assertRaisesRegex(RuntimeError, "private-provider-exception"):
                provider.invoke(request())
            record = metrics.finish(elapsed=timedelta(seconds=1))
            self.assertEqual(record.model_calls, 1)
            self.assertIsNone(record.model_tokens)
            self.assertNotIn("private-provider-exception", repr(record))


if __name__ == "__main__":
    unittest.main()
