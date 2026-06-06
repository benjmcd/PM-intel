# 03 — Acceptance Gates

## Gate principles

A milestone is not complete because code exists. It is complete only when its behavior is verified, documented, and consumed through the intended contract.

## Universal gate checklist

- [ ] Tests pass.
- [ ] No live external calls in default test path.
- [ ] New behavior has at least one positive test.
- [ ] New behavior has at least one negative/degraded test where relevant.
- [ ] `WORKLOG.md` updated.
- [ ] Relevant docs updated.
- [ ] No stop gate violated.
- [ ] No lower-layer bypass introduced.

## Data gate checklist

- [ ] Raw payload is preserved.
- [ ] Payload has source venue/channel/type.
- [ ] Parser version is recorded.
- [ ] Unknown or malformed payloads fail safely.
- [ ] Derived records can be regenerated from raw or fixture evidence.

## Alert gate checklist

- [ ] Alert has rule ID and rule version.
- [ ] Alert has reason codes.
- [ ] Alert has severity and confidence.
- [ ] Alert has data-quality status.
- [ ] Alert has source evidence.
- [ ] Alert is deduped/suppressed where appropriate.
- [ ] Alert output is stable enough for snapshot/regression testing.
