# Concrete model-provider metrics

Wrap an explicitly configured provider in `InstrumentedModelProvider` and give
that wrapper to the existing `Reasoner`. Start the run's canonical
`ExecutionMetricsRecorder` before invoking, and finish it after the run.

Each wrapped invocation records one `ModelCallEvent`. A successful response
from the requested model contributes its exact reported `ModelUsage`. Failed
invocations, malformed/mismatched responses, and unexpected provider exceptions
contribute a call with unknown usage. Missing tokens and cost remain unknown;
an earlier successful call's subtotal never masquerades as a full-run total.
Provider results pass through unchanged and are still validated by Reasoner.

The recorder receives model identity and usage only. Prompts, response content,
credentials, and exception strings do not become metrics. The wrapper neither
grants authority nor substitutes metric counts for kernel resource accounting.
The owning run must use its recorder exclusively until a call returns; the
recorder is not a shared concurrent session. Configuration errors in recording
are explicit errors, never silent loss of measurement.

Five loopback HTTP integration regressions exercise the actual
`HttpModelProvider` plus canonical metrics. This implements the AX-196 provider
connection, but does not constitute real-service credentials, a live reasoning
benchmark, measured learning, or AX-204/205 acceptance. Provider latency remains
in the canonical response usage; whole-run elapsed time belongs to the metrics
session's clock.
