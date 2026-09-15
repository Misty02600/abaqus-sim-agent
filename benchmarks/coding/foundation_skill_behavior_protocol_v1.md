# Linear Structural FEA Foundation Skill Behavior Protocol v1

This experiment asks a different question from `skill_routing_protocol_v1`: **does exposing the five deferred foundation Skills improve engineering behavior on the same frozen tasks?** Skill loading is recorded as cost/routing evidence, not treated as a correctness requirement.

## Frozen task source

Use the already sealed `skill_routing_cases_v1.jsonl` task text and seed files unchanged. Bind every batch to its existing seal. Do not reinterpret `required_loads` as behavior ground truth; the routing experiments already showed that a model can answer some cases correctly without loading a Skill.

Behavior criteria are separately predeclared in `foundation_skill_behavior_rubric_v1.json`. Any change to a criterion, task text, Skill body, Agent assembly, model profile or task instructions creates a new experimental binding.

## Profiles

Compare two explicit profiles with the same task instructions, model, budgets and frozen cases:

- `control`: product-default foundation exposure (`foundation_skill_profile="off"`), while the existing `abaqus-2024-coding` deferred Skill remains available;
- `foundation`: `foundation_skill_profile="linear_structural_fea_v1"`, exposing the same coding Skill plus the five foundation Skills.

Do not enable Catalog, Concept retrieval, diagnostic retrieval, Planning or Abaqus runtime in this experiment. The task surface stops at `analysis.json` or an explicit missing-input stop.

## Primary outcome: behavior adherence

Review the complete `analysis.json`, final decision/outcome and relevant trace against every predeclared case criterion. A reviewer records `pass`, `fail`, or `unknown` with evidence. Do not grade semantic engineering quality by keyword matching, by final host status alone, or by whether a Skill was loaded.

Host annotations are bound to the immutable batch `manifest.json` and `report.json` hashes, and the audit rechecks each per-case observation before aggregating the rubric. The audit also applies the zero-runtime experiment constraint independently of semantic scoring.

The important cross-case behaviors include:

- no invented user-specific physical input;
- no unsupported universal mesh, convergence or energy thresholds;
- preservation of already accepted model meaning in narrow edits;
- correct separation of result positions, extrapolation/averaging and QoI semantics;
- correct distinction among solution verification, physical validation and host acceptance;
- explicit separation of workspace facts from unavailable release-specific evidence;
- assumptions labeled as assumptions rather than silently promoted to user facts.

The missing-input case can count as `appropriately_stopped` even though the offline scripted clarification provider has no answer to supply. That host-level lack of an answer is not itself an engineering-behavior failure.

## Secondary outcome: routing/context cost

For every trial record:

- ordered and unique `load_capability` calls;
- unexpected/forbidden loads according to the original routing labels, as a descriptive routing signal only;
- duplicate load calls;
- model requests, input/output tokens and wall time when available;
- total byte size of unique loaded Skill bodies as a deterministic context-size proxy.

`required_load_recall` and `exact_route_pass` from routing v1 are not behavior metrics and must not gate a trial's engineering score.

## Repetition and comparison

The target comparison is three isolated repeats per profile over all eight frozen cases. Compare paired case-level behavior outcomes and costs across profiles, while preserving failed/time-out/provider trials in the all-started denominator. Small counts remain development evidence; do not promote the foundation profile to the product default from one batch or from a routing-only improvement.

Only consider default exposure after repeated evidence shows a behavior gain that is not offset by material scope drift, fabricated claims, or context/runtime cost.
