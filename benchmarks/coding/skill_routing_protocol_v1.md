# Linear Structural FEA Skill Routing Smoke v1

Status: frozen pre-treatment behavior set. The cases in `skill_routing_cases_v1.jsonl` are defined before exposing the five foundation Skills to the product Agent.

The frozen file binding is recorded in `skill_routing_cases_v1.seal.json`. Changing any case text or routing label requires a new benchmark version rather than updating the v1 seal after observing model behavior.

This smoke measures deferred Skill selection only. It does not measure Abaqus runtime success, physical correctness of a generated model, Catalog retrieval quality, or host-verifier acceptance.

## Capability set under test

The candidate treatment exposes these deferred capability names together:

- `abaqus-2024-coding`
- `linear-structural-fea-formulation`
- `linear-structural-fea-modeling`
- `linear-structural-fea-discretization`
- `linear-structural-fea-postprocess`
- `linear-structural-fea-verification`

The current product control remains unchanged and exposes only `abaqus-2024-coding`.

## Observation

For each case, record the ordered `load_capability` calls and the set of unique capability IDs loaded before the final answer. Repeated loading of the same capability should be recorded but counted once for set-level routing metrics.

The case file predeclares three sets:

- `required_loads`: capabilities whose body is needed for the intended engineering decision;
- `allowed_loads`: capabilities that may be loaded without counting as over-routing; v1 deliberately keeps these equal to `required_loads` so the first smoke has an exact target;
- `forbidden_loads`: capabilities whose loading is unrelated to the unresolved decision in that case.

No case may be re-labeled after seeing a live model trajectory. A future ambiguity discovered by review should create a new benchmark version.

## Metrics

For each case compute:

- required-load recall: `|loaded ∩ required| / |required|`, with an empty required set passing when nothing is loaded;
- forbidden-load count: `|loaded ∩ forbidden|`;
- exact-route pass: all required capabilities were loaded and no capability outside `allowed_loads` was loaded;
- load-call count: total `load_capability` calls, retaining duplicates as an efficiency signal.

The first smoke is descriptive. Do not promote the treatment to the product default from one small batch. Inspect under-loading and over-loading by case shape first.

## Execution boundary

Use tasks that stop at `analysis.json` or another non-runtime artifact. Keep Abaqus runtime tools, Catalog lookup, Concept retrieval, Solver diagnostic advisory, and physical oracle checks out of this routing experiment. The local-name case intentionally expects zero Skill loads; the API case intentionally expects only `abaqus-2024-coding`.

Missing user-specific physical inputs remain a clarification boundary. A routing pass does not excuse inventing a value or continuing into dependent modeling decisions.
