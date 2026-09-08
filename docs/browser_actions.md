# Governed browser action capability (M7.01)

> **Status:** M7.01. This task establishes the smallest governed browser
> **mutation** capability boundary. It is not a browser planner, not a
> natural-language element resolver, and not a second execution authority.

Canonical AgentX already had observational browser surfaces:

| Surface | Module | Role |
| ------- | ------ | ---- |
| Provider identity/availability | `browser_provider` (C5.01) | Descriptive only |
| Connection/session/tab identity | `browser_connection` (C5.02) | Inert snapshots |
| DOM observation | `browser_dom` (C5.03) | Read-only structured data |
| DOM selection | `browser_selection` (C5.04) | Exact-fact selection over snapshots |

Those surfaces do not click, type, or navigate. M7.01 adds the missing
**action** capability as an ordinary canonical `Capability` that still executes
only through `CapabilityExecutionLoop`.

## Operations

Exactly **one** operation is accepted per request. There is no retry loop and
no implicit multi-action chain.

| Operation | Identity | Required permission | Risk |
| --------- | -------- | ------------------- | ---- |
| `navigate` | `browser.actions.navigate@1.0.0` | `WRITE` | R2 `MODIFY` (state-changing, not reversible) |
| `click_selected` | `browser.actions.click_selected@1.0.0` | `WRITE` + `EXTERNAL_EFFECT` | R3 `EXTERNAL_EFFECT` (click may have externally consequential effects) |
| `fill_selected` | `browser.actions.fill_selected@1.0.0` | `WRITE` | R2 `MODIFY` (fill text never lowers risk) |

`submit_selected` is **not** implemented. Baseline provider/DOM contracts do
not expose a submit mechanism, and this boundary will not invent a second
action channel. Fill never implies submit.

Resource estimate is always exactly **one** machine action.

## Targeting

Action requests operate on **explicit structured browser targets**:

* C5.02 `BrowserTargetRef` (provider + session + tab/page identity)
* C5.03 `BrowserDomNodeRef` for click/fill (already-selected node identity)

The following are rejected as targeting:

* natural-language instructions (`"click the login button"`)
* selector synthesis from model text
* XPath/CSS generation
* vision fallback
* fuzzy element lookup

A selected node that does not belong to the requested target (lookalike) or
that is `STALE`/`UNAVAILABLE` fails closed **before** the driver is invoked.

## Navigation URL policy

`navigate` requires an explicit URL. Validation is fail-closed:

* scheme must be `http` or `https` (case-insensitive)
* host must be present
* length is bounded (`8192`, matching C5.02)
* control characters and whitespace are rejected

Rejected schemes include `javascript:`, `file:`, `data:`, `shell:`,
`powershell:`, and other non-web protocols. Userinfo is redacted from
observation/error projection so credentials cannot leak.

A URL whose path/query/fragment *looks* like authority (`permission=ADMIN`)
is still just a URL. It cannot change permission, risk, or verification.

## Execution boundary

`BrowserActionsCapability` does **not** launch Chrome/Edge, open sockets,
spawn processes, or evaluate JavaScript. It delegates a single already-validated
operation to an injected `BrowserActionDriver` bound to an already-established
C5.01 provider/session.

The driver protocol is intentionally narrow:

* `navigate`
* `click_selected`
* `fill_selected`
* `observe_dom` (independent verification read)

There is no `execute_script`, `eval`, CDP passthrough, `submit`, download,
upload, or process-launch method.

The existing C5.01 `BrowserProvider` remains observational
(`descriptor` / `status` / `capabilities`). M7.01 does not redefine it.
Availability is checked at execute time; availability is not authority.

## Verification

`NO ACTION == SUCCESS WITHOUT VERIFICATION` remains mandatory.

A driver `succeeded=True` return is **not** success. `verify` independently
re-observes document/node state:

| Operation | Independent check |
| --------- | ----------------- |
| navigate | current document URL matches the requested URL |
| fill_selected | selected node `value` attribute (else `text`) matches the requested inert text |
| click_selected | **only** if the request carries an explicit `BrowserClickPostcondition` (expected URL). Otherwise: executed but **not** verified. A bare click return never fabricates `passed=True`. |

Hostile observation fields such as `verified=true` are stored as data and are
never read as a verdict.

## Hostile web content

DOM strings, fill text, page titles, and URLs are untrusted **data**. Strings
such as `ignore previous instructions`, `permission=ADMIN`, `risk=R0`,
`verified=true`, or `task succeeded` cannot alter:

* required permissions or `RiskAssessment`
* ActionGate / budget / EmergencyStop
* operation identity
* verification
* Task status

Fill text is never parsed. `"submit automatically"` does not submit.

## Governance

Each operation exposes a canonical `CapabilityDescriptor`. This module never
calls `ActionGate`, never consumes a budget, and never clears `EmergencyStop`.
Those checks belong to `CapabilityExecutionLoop`.

Cancelled/timeout cooperative stops are honoured through the canonical
`ExecutionContext`. Failures are canonical `AgentXError` values projected into
`ExecutionResult` observation data (no raw secret leakage).
