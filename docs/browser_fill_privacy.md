# Browser fill privacy and independent readback repair

The canonical `browser.actions.fill_selected@1.0.0` already provides explicit
selected-node text entry. This repair reuses that path rather than adding a
second form capability. The N2.27 handoff forbids implicit submission.

Previously raw fill text appeared in parameter serialization, dataclass repr
and execution observations; canonical runtime events could therefore retain it.
Provider failure messages could echo the same material. These fill surfaces
now redact text and sanitize provider errors. The original text reaches only
the existing driver call and the in-memory independent comparison through the
capability's normal typed request. This is not a memory-zeroization guarantee.
Callers must not put secrets in task objectives, target metadata or URLs.

Verification now requires explicit field-value evidence. Matching general DOM
text without a `value` attribute is insufficient; provider adapters must expose
the current field value through the canonical observation contract. A driver
fill return alone is never verification. No submit, Enter, click or navigation
step is added.

Regression coverage checks request/observation redaction, provider error
sanitization, matching page text without value evidence, and the real governed
loop's events and audit records. Browser transport remains a deterministic fake
in these tests. This repair does not claim complete N2.27 field-type acceptance,
a concrete desktop browser driver, or a live browser workflow.
