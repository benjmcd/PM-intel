# Fast advance mode

Use this file when the user asks an agent to advance the repo quickly toward a usable local product, reduce ceremony, or avoid constrictive governance.

## Purpose

The repo should help an agent move faster, not trap it in process. Bottom-up sequencing, plans, and reviews are defaults for non-fragility. They are not excuses to avoid useful implementation when the next productive step is obvious; bottom-up is not a rigid lock.

## Authority in fast-advance work

1. Safety, honesty, legal/compliance boundaries, local-only exclusions, and no-trading/order-placement boundaries remain binding.
2. Passing executable checks and preserving data integrity outrank paperwork.
3. Working local utility outranks speculative future productization.
4. Material results and material local product progress outrank low-velocity ceremonial procedure.
5. Orthogonal investigation is encouraged when architecture, organization, orchestration, data semantics, or product utility is unclear.
6. Non-trivial decisions should use the compact Talmudic debate in `docs/governance/12_decision_methods.md`.
7. Docs/plans should be updated only when they clarify future work or preserve a material decision.
8. When this file conflicts with older process wording, prefer this file and update the stale wording.

## Meaning of production-grade in this repo

Production-grade means reliable local behavior: correct data lineage, replayability, tested degraded paths, clear operator commands, durable Postgres storage, and explainable alerts. It does not mean SaaS, hosted deployment, billing, user accounts, registry publication, or external service integrations.

## Default mode

Default to bottom-up implementation because this product depends on data lineage:

```text
raw evidence -> normalized records -> metrics -> alerts -> local delivery -> replay/backtest -> hardening
```

The agent may temporarily work top-down or in parallel when that is the fastest way to discover a missing contract, prove a vertical slice, or make the tool locally usable. Any spike must be paid back by adding or updating tests, contracts, schema, fixtures, reports, or a precise blocker before downstream code depends on it.

## Reasoning mode for unclear work

When the problem is architectural, organizational, orchestration-heavy, data-semantic, or otherwise unclear, do not stay trapped in the first obvious frame. Use the decision method in `docs/governance/12_decision_methods.md`:

```text
Question -> strongest case -> objection/failure mode -> orthogonal alternative -> consensus -> payback artifact -> next check
```

Keep this proportional. It can be five bullets in `WORKLOG.md` or the active plan. The point is to avoid narrow-centric tiptoeing and fragile local-optimum choices while still ending in material implementation.

## Good fast-advance behavior

- Run `python scripts\verify.py` at the start unless the environment is not installed yet.
- Use `python scripts\task.py status` to identify likely next work.
- Pick the highest-leverage local step, not necessarily the earliest unchecked box.
- Prefer one thin vertical slice that proves the product path over broad scaffolding.
- If Docker Desktop/Postgres is unavailable, do not stall. Mark the blocker and advance fixture-only repository interfaces, SQL review, fake-backed tests, or replay contracts.
- If a live venue adapter is blocked by API uncertainty, add fixtures/interface tests and document the live blocker.
- If the next step is unclear, run a short orthogonal/Talmudic decision pass, then build.
- Keep plans short and current. Do not rewrite large docs merely to satisfy process.
- When a change is small and obvious, implement it and record the result afterward.

## Acceptable top-down spikes

Allowed when bounded and then converted into proven lower-layer work:

- CLI workflow prototype that shows the target operator experience.
- Fixture replay path that reveals missing schema fields.
- Alert payload example that reveals missing metric contracts.
- Local report/dashboard sketch that clarifies what data tables must provide.
- Adapter interface sketch that reveals venue-normalization gaps.
- Orthogonal decision spike that chooses between materially different architecture or orchestration paths.

Not acceptable:

- hidden live API calls in normal verification;
- alerting without raw evidence lineage;
- scoring that cannot be replayed;
- local-only exclusions becoming implementation work;
- deleting or weakening checks to move faster;
- governance updates with no effect on executable truth, material design clarity, or local utility.

## State-aware next-action rule

At any repo state, choose the next action that maximizes:

```text
usable local product progress
+ reduction of architectural/data uncertainty
+ new executable verification
+ improved modularity/non-fragility/scalability
- irreversible complexity
- dependency on unverified external systems
- ceremonial work that does not change executable truth
```

When two options are close, choose the lower layer first. When the framing itself is unclear, use the orthogonal/Talmudic-style pass to expose the tradeoff quickly. When a lower layer is blocked by environment constraints, advance the nearest mock/fixture-backed contract and leave a precise blocker.

## Handoff standard

Before stopping, leave enough durable state that a new agent can continue without chat history:

```text
- what changed;
- what passed/failed;
- which milestone or vertical slice is next;
- any blocker and the exact command/evidence;
- any assumption that future code now depends on;
- any material orthogonal/debate consensus, if used.
```
