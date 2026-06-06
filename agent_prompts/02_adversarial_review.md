# Adversarial review prompt

Review the current diff as a skeptical second model. Focus on hidden coupling, missing tests, schema fragility, live API leakage into normal tests, premature infrastructure, context bloat, and violations of adaptive bottom-up evidence discipline. Return file-specific findings and the next highest-leverage verified slice.


Also check local-only exclusions: no SaaS/billing/hosted deployment/registry publication/signing/managed-secret/full auth/RBAC/OIDC/cloud/external CI work unless explicitly approved by a new ADR.
