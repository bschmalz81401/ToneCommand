# ToneCommand Architecture: scaling beyond one box

Today: Fractal FM9. In flight: HeadRush (community port). Tomorrow: ToneX,
Kemper, Boss ES-5. Eventually, someone's fridge. This document defines the
seams that make that growth safe.

## The core insight

A week of hardware work taught us which parts of this system are
device-specific and which are the product:

**Device-specific (swappable):** the wire protocol, the parameter catalog,
the quirks (async writes, aliased IDs, silent refusals), the enum
ordinals, the simulator that models them.

**The product (invariant):** natural language in, verified hardware state
out, under a safety contract:
1. Grounding: model names resolve to real-world gear, facts-only, cited,
   never invented. Unknowns stay unknown and say so.
2. Validate before send: every plan is checked against the device's own
   schema before a single byte reaches hardware.
3. Explicit confirmation: nothing is stored without the owner saying so,
   to targets the owner whitelisted.
4. Read-back truth: every write is verified by reading the device, and
   read paths themselves are ranked by how honestly they reflect audible
   reality. Ear checks outrank reads for anything audible.
5. Undecoded-territory honesty: operations no hardware session has
   verified are named, not silently simulated.

A new device earns support by implementing the swappable layer and
inheriting the invariant one. The invariants are NOT renegotiable per
device; that is what makes the system trustworthy at any scale.

## The device adapter contract

Each device family lives in its own package and satisfies one interface
(see fm9/adapter contract in code):

- identify() -> device kind, firmware, capabilities
- catalog() -> the device's parameter/block/model schema, snapshot-able
  so the app never needs live hardware at runtime
- grounding() -> facts-only sidecars mapping the device's model names to
  real gear, with drift guards that fail loudly when the catalog moves
- read_state() -> current preset/scene/blocks/values, from the read paths
  proven honest for that device
- apply(actions) -> validated, verified writes; returns per-action
  results with read-back evidence
- store(target) -> whitelisted, confirmation-gated persistence
- simulator() -> a frame-level sim modeling that device's real quirks,
  with undecoded-territory tracking

The planner, validator, server routes, and UI speak ONLY this contract.
Device packages never import each other.

## The planner stays device-blind

The planner receives: a state text, a parameter reference, and an action
vocabulary - all produced by the adapter. It never sees SysEx. Backends
are interchangeable (Claude CLI, API, any OpenAI-compatible endpoint,
issue #7): every backend's output passes the same device-grounded
validator, so backend quality is a quality problem, never a safety one.

## What porting actually takes (evidence: the HeadRush port)

The community HeadRush port (@bschmalz81401) independently converged on
the same seams: snapshot the device's self-describing schema, verify
every AI reply against the snapshot, gate hardware writes behind
preflight + explicit confirm, test against a stateful stub unit. That
convergence is the strongest evidence the contract above is the right
one. His port is the template for adapter #2.

## Migration path (non-breaking)

1. Contract as code: a typed adapter protocol; FM9 + its simulator
   certified against it by tests. (landed with this document)
2. Server routes take a device handle instead of importing fm9 directly;
   single-device deployments see no change.
3. Grounding sidecar schema formalized (shared JSON shape + drift-guard
   convention already proven across amps/drives/cabs/effect types).
4. Adapter #2 (HeadRush, upstream or sister-repo) validates the contract;
   whatever it breaks, the contract fixes.
5. Only then: multi-device sessions, cross-device tone translation
   ("make my Kemper sound like this FM9 preset") - the payoff features
   that only a clean seam makes possible.

## What we refuse to do

- No lowest-common-denominator abstraction: adapters expose device
  superpowers (FM9 scenes, channels) through capability flags, not by
  pretending all devices are equal.
- No speculative interfaces for devices nobody is porting. The contract
  grows when a real port stresses it, per the project's never-invent
  rule - which applies to architecture too.
