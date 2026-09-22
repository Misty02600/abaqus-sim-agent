# Structural Mechanics Topic Skill Routing Smoke v1

Status: frozen development routing set. The cases in
`structural_topic_skill_routing_cases_v1.jsonl` are defined before any live model trial for
this benchmark version. Their binding is recorded in
`structural_topic_skill_routing_cases_v1.seal.json`.

This first-layer smoke measures deferred Skill selection only. It does not measure Abaqus
runtime success, engineering-answer quality, physical correctness, Catalog or document
retrieval quality, or host-verifier acceptance.

## Capability set under test

The treatment exposes `abaqus-2024-coding` plus the structural-mechanics topic profile:

- `hyperelastic-fea`
- `hyperelastic-calibration`
- `plasticity-fea`
- `geometric-nonlinear-fea`
- `structural-dynamics-fea`
- `modal-dynamics-fea`
- `explicit-dynamics-fea`
- `quasi-static-explicit-fea`
- `contact-fea`
- `contact-diagnostics-fea`
- `interface-cohesive-fea`

The five linear-structural foundation Skills are disabled in this experiment so topic-routing
labels remain interpretable. Catalog lookup, Concept retrieval, diagnostic retrieval, planning,
and every Abaqus runtime route are also disabled.

## Case design

v1 uses one deliberately narrow intent for every topic Skill, one Abaqus-specific API case that
should route only to `abaqus-2024-coding`, and one workspace-local case that should load no Skill.
No v1 case intentionally requires two topic Skills. If later review shows a case is genuinely
ambiguous, create a new benchmark version instead of relabeling this frozen set after observing a
model trajectory.

Each case predeclares:

- `required_loads`: Skill bodies needed for the intended unresolved decision;
- `allowed_loads`: Skill bodies that do not count as over-routing; v1 keeps this equal to
  `required_loads` for an exact first smoke;
- `forbidden_loads`: every other exposed capability.

## Observation and metrics

Record ordered `load_capability` calls and the set of unique loaded capability IDs. Repeated loads
remain visible as routing cost but count once in set-level metrics.

For each case compute:

- `required_load_recall`;
- `forbidden_load_count`;
- `unexpected_loads`;
- `load_call_count`;
- `duplicate_load_call_count`;
- `exact_route_pass`.

Also record the byte size of unique known Skill bodies loaded as a deterministic context proxy.
Provider request/token usage remains whatever the CodingRun state records; this benchmark does not
convert routing success into an engineering-quality score.

## Execution boundary

Each task stops at `analysis.json` or an explicit missing-input decision. Runtime budgets are zero,
and the report records any attempted runtime call as a protocol violation signal. The evaluator
binds the case file, seal, evaluator implementation, Agent assembly implementation, and exposed
Skill bodies by hash before the live run and verifies the bindings again afterward.

The first smoke uses one repeat and is descriptive. A high routing score alone is not evidence that
the Skills improve modeling decisions; engineering behavior belongs to the next evaluation layer.
