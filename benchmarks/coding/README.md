# Abaqus 2024 closed-loop smoke evaluation

The ordinary product CLI is separate from these fixed oracles. A real non-plate
smoke created `make_cube.py`, `cube.cae`, and `cube.inp`, declared exactly those
deliverables, ran one CAE stage and zero Solver stages, and completed with
`verification_scope=execution_evidence`. It did not run a physics oracle. Frozen
evidence: `product_open_cube_execution_evidence_20260831.json`.

The first Gold case starts from an empty workspace and intentionally omits the
plane-stress thickness. The Agent must ask for `plate_thickness` before any
Abaqus invocation, consume the scripted `1 mm` answer, generate and solve the
model, extract ODB results, and pass the independent physics oracle.

## Physics-materiality clarification contract

`plate_clarification_policy_cases.jsonl` freezes four request-level behavior
cases for `physics-materiality-clarification-v1`: an Agent-owned element choice,
an ambiguous plane-stress/plane-strain intent, a compatible explicit element
constraint, and a conflicting explicit constraint. The decision-only evaluator
passed the ambiguity case 3/3 and the conflict case 2/3 with no pre-decision
tools or workspace files; the remaining conflict attempt was a provider timeout.
It deliberately has zero Abaqus budget and therefore proves no closed loop.

The same plate oracle was used independently for the two `proceed` cases. The
Agent-owned element-choice batch ended in three provider timeouts and passed 0/3.
The compatible explicit CPS4R batch passed one complete oracle run and then had
two provider timeouts, so it passed 1/3. A separate strict missing-thickness Gold
also passed 1/3. None of these three licensed batches met the 2/3 gate. The exact
configuration, source hashes, scorer replay, per-attempt results, and allowed
claims are frozen in
`plate_clarification_policy_evaluation_20260830.json`.

A temporary `http_retries=10` / `http_timeout_seconds=60` follow-up reran both
licensed proceed cases without eval-level retry. Both remained at 1/3 and failed
gate. The element-derived pass asked no element question but initially chose CPS4
and reached the support-profile CPS4R requirement only after host feedback. The
explicit-element batch had one pass, one 9-call runtime-budget failure, and one
timeout. These separate stochastic batches do not isolate retry count as the
cause. See `plate_clarification_retry10_evaluation_20260830.json`.

The autonomous element-choice contract was then corrected to accept one uniform
choice of either CPS4 or CPS4R while keeping the explicit-element and Gold cases
exactly CPS4R. Deterministic verifier tests accept either single choice and reject
mixed types. The first fresh Product batch was provider-blocked 0/3 before files
or Abaqus calls, so it provides no model-choice result. Evidence:
`plate_element_choice_allowed_evaluation_20260831.json`.

A fresh follow-up kept that corrected contract and the same OpenCode Go,
`deepseek-v4-flash`, retry10/timeout60, automatic-final15, retrieval-off and
Planning-off profile. It explicitly enabled one same-run resume for a
`ModelAPIError`; pydantic-evals task retry remained disabled. All three attempts
hit a natural Provider timeout and resumed once. One resumed run then selected
80 uniform CPS4 elements without clarification and passed the complete licensed
oracle; the other two timed out again before files or Abaqus calls. The result is
therefore 1/3 and still below gate. It is positive evidence for bounded same-run
recovery and one valid autonomous choice, not a stable element-choice capability
or a causal resume uplift. Evidence:
`plate_element_choice_allowed_resume_once_evaluation_20260903.json`.

At the user's request, a second fresh time-separated batch repeated the exact
same profile without changing the retry or resume policy. Its first two runs
completed directly in turn 1: one chose 80 CPS4R elements and the other chose
80 CPS4 elements; both asked no element question and passed all artifact, Job,
U/S/RF and result-oracle checks. The third run timed out, resumed once, and
timed out again before files or Abaqus. This batch passed its own gate at 2/3,
while the preceding 1/3 remains preserved; the combined descriptive history is
3/6 and was not a preregistered pooled gate. Evidence:
`plate_element_choice_allowed_retry_batch_evaluation_20260903.json`.

The interrupted third run was then resumed directly after the batch, without a
new run ID or evaluation task. It entered turn 3 from the turn-2 `step_complete`
checkpoint and preserved the accumulated wall-clock budget, but the Provider
timed out again before files or Abaqus execution. The state remained
`interrupted` at 2465.483 of 2700 seconds. This post-batch continuation proves
that explicit resume is not limited by the evaluator's one-auto-resume policy;
it does not change the point-in-time 2/3 gate. Evidence:
`plate_element_choice_post_batch_manual_resume_20260903.json`.

The user then explicitly authorized a wall-clock contract amendment for that
same run. The total limit changed from 2700 to 5400 seconds while retaining
2465.483 seconds of accumulated active time. Turn 4 resumed the same history and
completed with 80 CPS4R elements, six Abaqus calls, one Solver call, fresh
artifacts and a passing host oracle. Its final active time was 2584.038 seconds,
which is still below the original limit; the amendment removed imminent budget
pressure but cannot be credited causally for success. This was a manual audited
evaluation-state amendment rather than a product interface at the time, and it
does not rewrite the original 2/3 batch gate. The typed product amendment layer
was implemented later under ADR-0021. Evidence:
`plate_element_choice_budget_extended_resume_20260903.json`.

The first real-Provider product E2E for that layer used one v3 run. A
deterministic first turn settled `write_file(seed.py)` and injected a connection
failure; the actual CLI then applied a typed operational amendment and OpenCode
Go continued the same history. After completion, a new goal created task
revision 1: the prior outcome was archived, prior files formed the new baseline,
and only modified/new files were fresh in the next product verification.

The run also reproduced a Harness 0.24.0 Windows race: parallel file tools used
separate worker threads to append `tool_effects.jsonl`, replacing complete JSON
records with `ll}`. A version-scoped patch now serializes event/effect appends;
the final turn produced 16 parseable records. That historical run used the
separate event log to recover old damage, but a later safety review found an
effect-start/event-start crash window; current code therefore rejects any corrupt
tool-effect ledger instead of attempting compatibility recovery. Both product execution-evidence
checks passed, but the model omitted requested trailing newlines while claiming
they existed. This therefore proves the state/revision and documented product
verification scopes, not full natural-language semantics. Frozen evidence:
`coding_run_amendment_revision_provider_e2e_20260903.json`.

Run the decision-only cases without Abaqus execution:

```powershell
uv run --group eval python -m abaqus_sim_agent.evaluation.clarification_policy `
  --run-root D:\abaqus-runs\clarification-policy
```

Run one licensed proceed case against the existing plate oracle:

```powershell
uv run --group eval python -m abaqus_sim_agent.evaluation.plate_tension `
  --clarification-policy-case plate-explicit-compatible-element `
  --run-root D:\abaqus-runs\plate-explicit-compatible-element
```

Run the licensed evaluation sequentially:

```powershell
$env:DEEPSEEK_API_KEY = "..."
uv run --group eval python -m abaqus_sim_agent.evaluation.plate_tension `
  --run-root var\runs
```

To use OpenCode Go explicitly:

```powershell
uv run --group eval python -m abaqus_sim_agent.evaluation.plate_tension `
  --model-provider opencode-go `
  --model deepseek-v4-flash `
  --run-root var\runs
```

The selected gateway and exact model ID are written into the evaluation metadata.
There is no automatic fallback between the official DeepSeek, OpenCode Go, and
OpenCode Zen endpoints.

The temporary current default gives each logical model request a 60-second HTTP
timeout and `--http-retries 10` (ten retries after the initial request). The
normal CodingRun wall budget remains 2700 seconds (45 minutes), while API-repair
cases keep an explicit 600-second task budget. New evaluation metadata records
both values. Frozen experiments retain their recorded historical retry profiles.
Set `--http-retries 0` for a raw single-request reliability baseline; Pydantic
Evals task and evaluator retries remain disabled.

The experiment repeats the case three times with `max_concurrency=1`; at least
two attempts must pass. This is a smoke evaluation, not a statistically stable
success-rate estimate.

## Earlier Prompt v2 Exact API Catalog ablation

The current `plate-tension-v2` task makes both required script names visible and
accepts any single analysis-step name. Run B0 and B1 in separate fresh roots,
with the Catalog outside both run roots:

```powershell
uv run --group eval python -m abaqus_sim_agent.evaluation.plate_tension `
  --run-root D:\abaqus-runs\catalog-ablation\b0 `
  --model-provider opencode-go `
  --model deepseek-v4-flash `
  --http-retries 2

uv run --group eval python -m abaqus_sim_agent.evaluation.plate_tension `
  --run-root D:\abaqus-runs\catalog-ablation\b1 `
  --model-provider opencode-go `
  --model deepseek-v4-flash `
  --http-retries 2 `
  --api-catalog-dir D:\path\outside-runs\abaqus-2024-catalog
```

The local same-configuration smoke observed B0 `0/3` and B1 `0/3`; all six
attempts terminated at the 30-request model budget. B0 reached the solver in
all three attempts, while B1 remained in CAE authoring in all three. B1 made
3–4 counted exact lookups per attempt and did not materially increase total
input tokens, but no closed-loop benefit was observed.

This does not establish a stable success rate or prove that API retrieval is
generally harmful. It does establish that an `11/11` resolver benchmark and
retrieval occurrence are not evidence of task completion. B1 therefore remains
explicit and disabled by default; each lookup is recorded in
`state/api_lookups.jsonl` for query, status, card IDs, counted status, and
payload size.

The implementation subsequently added cross-query card-ID deduplication after
the smoke exposed the same `FieldOutputRequest` card through qualified and bare
names. That post-experiment change has not yet been rerun and carries no new task
benefit claim.

## Prompt v3 baseline stabilization and Catalog ablation

Prompt v3 keeps the same 30 model-request and 9 Abaqus-call budgets, but makes
the loop cheaper and more evidence-driven:

- missing physics input returns before Skill, planning, inventory, or workspace tools;
- an empty case workspace disables RepoContext inventory;
- Planning exposes one concise `write_plan` call rather than granular updates;
- the Skill carries short official/reference-backed import, repository, Region,
  `findAt`, and ODB-field rules;
- the experiment used an unchanged-input hash guard and terminal budgets; the
  guard was later removed after a run-history audit found one low-value hit and
  several legitimate-retry false-positive risks;
- automatic API hints recover qualified receivers from runtime type and script AST.

The latest same-code smoke observed:

| Arm | Closed-loop pass | Gate | Counted lookup | Solver calls |
|---|---:|---:|---:|---:|
| B0 no retrieval | 3/3 | pass | 0 | 3 |
| B1 Exact API Catalog | 1/3 | fail | 6 | 2 |

B0 averaged 22.7 model requests and 7.3 Abaqus calls. B1's only passing run
made no Catalog lookup; its other two attempts each returned three exact cards
but did not complete the contract. This establishes the first passing Agent
baseline while still providing no evidence that the Catalog improves the task.
The Catalog remains explicit and disabled by default.

## Multi-API repair benchmark

The plate task does not reliably exercise a particular API, so it cannot by
itself distinguish “the Catalog was unhelpful” from “the run never needed a
lookup.” The repair benchmark freezes six existing-script failures and checks
the actual CAE-created postcondition. DEV has three consumer-specific runtime
notes; HOLDOUT has no runtime notes and covers three different official/abqpy
API families.

```powershell
uv run --group eval python -m abaqus_sim_agent.evaluation.api_repair `
  --run-root D:\abaqus-runs\api-repair-skill `
  --model-provider opencode-go `
  --api-catalog-mode off

uv run --group eval python -m abaqus_sim_agent.evaluation.api_repair `
  --run-root D:\abaqus-runs\api-repair-catalog `
  --model-provider opencode-go `
  --api-catalog-mode full `
  --api-catalog-dir var\api-catalog\abaqus-2024-final15

uv run --group eval python -m abaqus_sim_agent.evaluation.api_repair `
  --run-root D:\abaqus-runs\api-repair-notes `
  --model-provider opencode-go `
  --api-catalog-mode automatic_verified_only `
  --api-catalog-dir var\api-catalog\abaqus-2024-final15
```

All six checked-in broken fixtures fail in real Abaqus 2024, and repaired
oracles kept outside the Agent workspace pass the expected postconditions.
The first six-case smoke observed Skill/full/automatic as `5/6`, `6/6`, and
`6/6`. The three HOLDOUT cases were then interleaved for three rounds:
Skill-only was `8/9`, while full Catalog was `9/9`. Full made exactly one found
lookup per holdout and delivered zero runtime notes, so the holdout result tests
official/abqpy cards rather than memorized patches.

Across those nine holdout attempts, full used 11.4% fewer model requests, 9.6%
fewer input tokens, and 13.6% fewer tool calls in aggregate, mostly because it
avoided one long Skill-only FieldOutput failure. On ordinary successful paths,
the 1.7–3.0 KB Card can still increase input context. Treat this as observed
tail-risk reduction on three API families, not a stable universal success-rate
claim. Frozen cases and the summarized evidence are in
`api_repair_cases.jsonl` and `api_repair_evaluation_20260823.json`.

Two targeted regressions discovered after that frozen result stay in the
separate `api_repair_regression_cases.jsonl` manifest, so they cannot rewrite
the six-case smoke history:

| Failure behavior | Coverage | Evidence status |
|---|---|---|
| invented `Part.BaseSolidBox` | Product-Agent repair case `base_solid_box` | broken fixture and external minimal repair validated with Abaqus 2024; automatic final8 passed once in 8 model requests and 2 CAE calls after adopting the owner-aware hint |
| invalid `PartInstance.Set` owner | Product-Agent repair case `part_instance_set` | broken fixture and external minimal repair validated with Abaqus 2024; automatic final8 passed once in 6 model requests and 1 CAE call after repairing ownership before runtime |
| `findAt` shape and consumer-specific GeomSequence binding | reuse frozen `face_findat_keyword_multi`, `seed_nested_sequence`, `mesh_controls_nested_faces`, and `assembly_set_scalar_edge` | covered by the existing six-case Agent smoke; no duplicated fixture |
| edge-only `Assembly.Set` note must not match `faces=` | deterministic routing test `test_automatic_note_scope_rejects_faces_before_query_or_budget` | Chinese `faces` and indexed `edges` parsing covered; scope filtering happens before lookup and budget, while the seed-edge positive path remains covered |

Run the two Product-Agent regressions independently by adding
`--manifest benchmarks/coding/api_repair_regression_cases.jsonl` to the same
evaluation command and using the current immutable Catalog:

```powershell
uv run --group eval python -m abaqus_sim_agent.evaluation.api_repair `
  --run-root D:\abaqus-runs\api-repair-regression-final8 `
  --model-provider opencode-go `
  --api-catalog-mode automatic_verified_only `
  --api-catalog-dir var\api-catalog\abaqus-2024-final8 `
  --manifest benchmarks\coding\api_repair_regression_cases.jsonl
```

The historical final8 artifact contains 5,481 cards and 48 retained conflicts;
its fixed 12-query deterministic benchmark is `12/12`. Runtime notes declare
`applies_to_parameters`, so parameter scope is checked before a query is counted
or a note budget is consumed. Automatic `AttributeError` misses return compact,
owner-aware names only: `Part.BaseSolidBox` suggests
`Part.BaseSolidExtrude/Revolve/Sweep`, and `PartInstance.Set` suggests
`Assembly.Set/Part.Set`. These misses do not return cards, invoke vector search,
or consume the lookup/note budget.

The Windows CRLF and Shell-timeout regressions are locally mitigated without
copying Harness FileSystem or editing `.venv`: two version-scoped, idempotent
patches normalize `_content_hash` and replace the upstream POSIX `os.killpg`
cleanup with `taskkill /PID /T /F` for Harness 0.24.0. Direct file round-trip and
real Shell timeout tests pass. Runtime provenance remains in
`api_repair_regression_runtime_observations.json`; repaired scripts remain outside
the repository and are not Agent-visible. The frozen Product-Agent evidence is
[api_repair_regression_evaluation_20260824.json](api_repair_regression_evaluation_20260824.json).
The original two cases ran once under `automatic_verified_only` and passed. A
third DEV regression derived from the observed B31 `Model.BeamSection`
`orientation` TypeError was added on 2026-08-25. Its Skill contains no beam
solution and its Card has no runtime note. Under `automatic_exact_signature`,
the first failed CAE produced one 1,299-byte compact signature, the Agent removed
only the unsupported keyword, and the second CAE plus host verification passed.
All three cases are known-failure regressions run once, so `3/3` is not a
stable-rate or untouched-holdout claim.

## final15 verified-card audit and output convergence regression

The 2026-08-28 audit scanned 162 histories and 745 typed runtime calls, then
reproduced the selected boundaries in licensed Abaqus 2024 fixtures. The
current immutable final15 artifact has 5,480 cards, 48 retained source
conflicts, 16 verified runtime notes, and a 27/27 deterministic lookup
benchmark. Ten notes are new relative to final9; they cover cross-dimensional
`setElementType` dispatch, beam-orientation region binding, SIDE1/SIDE2 edge and
face Surface containers, B31 companion output fields, ODB field/value and node
array ownership, and BeamSection/orientation separation.

Candidate notes are not retained merely because their licensed micro-probe was
correct. The final11 SectionAssignment note used 6 CAE calls across three
automatic attempts versus 4 with Catalog off; DisplacementBC and
ConcentratedForce had no Product treatment and duplicated the Skill's normal
assembly-region path. All three were removed from final12/final13.

Focused Product evidence is deliberately mixed:

| Boundary | Off | Automatic | Interpretation |
|---|---:|---:|---|
| `Assembly.Set.edges` | 1/3, 14 CAE | 3/3, 6 CAE, note 3/3 | strong small-sample long-tail reduction for the older note |
| `Assembly.Surface.side1Edges` | 1/3, 11 CAE | 3/3, 9 CAE, note 2/3 | mixed positive; one pass diverted to probes before routing |
| `Part.setElementType.regions` | 3/3, 4 CAE | 3/3, 6 CAE, note 3/3 | correct fallback, no call saving on the simple fixture |
| `Model.BeamSection` orientation | 1/1, 1 CAE | 1/1, 1 CAE, note 0/1 | both repaired from source inspection before a failing run |

Two additional trajectories completed the API repair after two CAE calls but
then returned prose instead of the strict output tool. Pydantic's generic
`Invalid JSON` retry was misread as a workspace result-file error, leading to
BOM/newline/atomic-write probes until the 18/5 budget was exhausted.
`decision-output-tool-clarification-v1` now states that output-validation
errors refer to the final `CodingRunDecision`, never a workspace artifact. One
Assembly.Set and one Surface follow-up both passed with two CAE calls. These
are one-attempt regressions, not a stable-rate estimate.

The final13 Catalog then entered the real B31 from-scratch Gold for three
attempts. It passed 1/3 and did not meet gate: attempts 1 and 3 timed out before
files or Abaqus; the only task-started attempt passed the complete oracle with
24 model requests, 8 Abaqus calls, and one Solver. That run delivered the
FieldOutput ownership note, but the Agent repaired one `FieldOutput.position`
access and left a second occurrence, spending another extraction call. It also
spent the first CAE call on `BeamSection(beamOrientation=...)`. A separate
licensed probe then confirmed that ordinary BeamSection construction succeeds
while `orientation`, `beamOrientation`, and `geometricType` are all rejected;
final15 expands the existing note scope without adding a model template.
`same-api-occurrence-scan-v1` now asks the Agent to inspect all identical
proven-invalid accesses before rerunning, but has no Product savings evidence.

The time-neighbor final15 treatment then passed 0/3. Its only task-started
attempt used 31 model requests and all 12 Abaqus calls without reaching Solver;
after adopting the BeamSection and beam-orientation notes, it shifted into
independent-instance, mesh ownership, Assembly Set, and probe/check-script
failures. The other attempts were a pre-file HTTP 500 and timeout. This batch
also used the pre-fix over-broad route that mapped CAE `MeshNodeArray.findAt`
to the ODB-only `openOdb` note. Current source accepts only the exact
`OdbMeshNodeArray` type and passes deterministic routing tests, but there is no
post-fix B31 Product evidence in this batch. This failure motivated the bounded
runtime-file policy below; it does not rewrite the 0/3 result.

The next bounded treatment added a strict task-owned runtime-file allowlist:
only `build_model.py` may enter CAE, `beam_tension.inp` may enter Solver, and
`extract_results.py` may enter Abaqus Python. Denials occur before probe state,
budget reservation, or runner execution. This v7 batch passed 2/3. All three
attempts entered the task; one exhausted 31/12/0 model/Abaqus/Solver calls,
while the passing attempts used 9/3/1 and 11/4/1 and passed every host oracle.
No attempt called `run_command`, created a vendor-runtime diagnostic script, or
triggered a policy denial. This proves the combined v7 profile and compliant
normal paths, while deterministic tests prove denial semantics; it does not
prove the allowlist alone caused the adjacent 0/3 to 2/3 change.

Run the new BeamSection fallback case independently with:

```powershell
uv run --group eval python -m abaqus_sim_agent.evaluation.api_repair `
  --run-root D:\abaqus-runs\api-repair-final13-beam `
  --model-provider opencode-go `
  --api-catalog-mode automatic_verified_only `
  --api-catalog-dir var\api-catalog\abaqus-2024-final15 `
  --manifest benchmarks\coding\api_repair_final13_new_cards.jsonl
```

Frozen evidence is in
[`api_repair_final13_evaluation_20260828.json`](api_repair_final13_evaluation_20260828.json);
the real B31 batches are
[`beam_tension_b31_final13_evaluation_20260828.json`](beam_tension_b31_final13_evaluation_20260828.json)
and
[`beam_tension_b31_final15_evaluation_20260828.json`](beam_tension_b31_final15_evaluation_20260828.json);
the accepted v7 profile is
[`beam_tension_b31_runtime_allowlist_evaluation_20260829.json`](beam_tension_b31_runtime_allowlist_evaluation_20260829.json);
the catalog/probe provenance is in
[`../api_catalog/abaqus_api_catalog_final15_20260828.json`](../api_catalog/abaqus_api_catalog_final15_20260828.json).

## Historical advisory and enforced Abaqus ACI

This section preserves superseded experiments. ADR-0038 removed both execution
modes from the current product; their old configuration and results remain
historical evidence rather than a runnable current profile.

The optional preflight does not install abqpy into Abaqus. It unpacks the pinned
2024.10 wheel outside the Agent workspace and runs BasedPyright before a new
production CAE or extraction script hash enters vendor runtime. Raw diagnostics
are preserved, but only a versioned high-confidence subset is returned to the
Agent. The first report consumes no Abaqus budget; unavailable/timeout checks
fail open, and an unchanged retry remains an evidence-only false-positive escape.

The v2 policy audit observed zero pauses across 21 checked-in Abaqus 2024
positive scripts while filtering 134 raw diagnostics, and caught all five fixed
licensed-failure scripts. Mean local time was about 1.18 seconds per script.
This is a bounded precision fixture, not a general static-correctness claim.

The first Product treatment kept the B31 request, Go/Flash, final15 automatic,
runtime allowlist, budget, and verifier fixed. It passed 2/3 with one pre-file
transport timeout. One passing run received five actionable reports for invalid
keywords/attributes, but repeated each unchanged and let Abaqus confirm the same
errors; it still used nine Abaqus calls. The other passing run had no actionable
report and used four calls. Thus v1 proves detection and clean-path compatibility,
not call savings or gate benefit. v2 asks the Agent to batch all supported repairs
and also accepts missing-required-parameter diagnostics, but its formal batch had
three pre-file Go timeouts and no preflight/runtime calls, so Product adoption is
still unknown. The accepted B31 profile remains v7 with preflight off.

The accepted replacement is an explicit `enforced_verified` ACI profile. It
uses a Harness tool guardrail to reject invalid Python 3.10 production-file
candidates before they change the workspace, rechecks the current hash after
runtime authorization, and hard-blocks only diagnostics with frozen licensed
evidence. The model cannot acknowledge an unchanged blocker; unverified
attributes and selected SWIG/container mismatches are warnings. Checker failure
is fail-closed in this mode, while the historical advisory remains available.

The enforced offline audit observed 0 blocked scripts and 16 warnings over the
same 21 positive scripts, 5/5 blockers over the licensed negative set, and 4/4
blockers for additional B31 runtime failure families. Its one formal B31 batch
had three pre-file Go timeouts and zero syntax/API/Abaqus calls, so it supplies
no Product adoption evidence and does not replace v7. No fourth attempt was run.

The historical enforced treatment was launched with:

```powershell
uv run --group eval python -m abaqus_sim_agent.evaluation.beam_tension `
  --run-root D:\abaqus-runs\beam-b31-static `
  --model-provider opencode-go `
  --api-catalog-mode automatic_verified_only `
  --api-catalog-dir var\api-catalog\abaqus-2024-final15 `
  --runtime-script-policy-mode production_allowlist `
  --static-preflight-mode enforced_verified `
  --abqpy-static-wheel var\api-catalog\sources\abqpy-2024.10-py3-none-any.whl `
  --static-preflight-cache-dir D:\abaqus-runs\static-preflight-cache
```

Frozen evidence is in
[`abqpy_static_preflight_policy_v2_20260829.json`](abqpy_static_preflight_policy_v2_20260829.json)
and
[`beam_tension_b31_abqpy_preflight_evaluation_20260829.json`](beam_tension_b31_abqpy_preflight_evaluation_20260829.json).
The replacement evidence is in
[`abaqus_aci_enforced_policy_v1_20260829.json`](abaqus_aci_enforced_policy_v1_20260829.json)
and
[`beam_tension_b31_aci_enforced_evaluation_20260829.json`](beam_tension_b31_aci_enforced_evaluation_20260829.json).

## Current non-blocking runtime feedback pilot

The current contract always checks Python 3.10 syntax before CAE/Abaqus Python
execution. Optional BasedPyright analysis has only `off`/`on`; diagnostics and
analyzer availability accompany the actual runtime result and never form a
separate acknowledgement or API blocker.

The frozen v1 pilot ran off/on in reversed order over two repetitions with Catalog off. See
[`runtime_feedback_repair_protocol_v1.md`](runtime_feedback_repair_protocol_v1.md) and the
[qualification record](runtime_feedback_repair_qualification_20260923.md). Qualification established 4/4 licensed broken
baselines and a two-hit/two-miss static stratum. The subsequent
[real-model exploratory paired pilot](runtime_feedback_repair_paired_v1_20260923.md)
produced 1 win / 7 ties / 0 losses, with lower request/runtime counts but higher
token usage. Its source seal did not cover the complete Agent chain, so it keeps
static feedback default off and does not expand to full mechanics.

The current runner emits the [v2 protocol](runtime_feedback_repair_protocol_v2.md): both arms bind the same required
Catalog in `automatic_verified_only`, while only BasedPyright remains off/on. It does not alter or combine the v1 evidence.
Use `python -m abaqus_sim_agent.evaluation.runtime_feedback_repair --help` for the required paths.

## Corrected from-scratch Catalog ablation

The old plate B1 never delivered a verified runtime note, and one near-complete
attempt spent its final request updating the plan. Prompt v4 applies those
shared fixes and compares all three modes with Planning disabled:

| Arm | Pass | Model requests | Input tokens | Abaqus calls |
|---|---:|---:|---:|---:|
| Skill-only | 2/3 | 71 | 920,604 | 21 |
| full Catalog | 2/3 | 65 | 883,854 | 20 |
| automatic verified notes | 3/3 | 54 | 597,995 | 18 |

The automatic arm delivered three notes across two attempts; its other passing
attempt used no lookup. Full reduced the first two successful trajectories to
17/7 and 18/6 model/Abaqus calls, but its third attempt failed after three
Solver invocations, showing that exact API evidence does not solve modeling or
solver-loop failures. The frozen report is
`plate_tension_catalog_evaluation_20260823_v4.json`. Three stochastic attempts
of one plate task remain a smoke result, not evidence that this mode will win on
all from-scratch models.

## 3D bar from-scratch smoke evaluation

The second Gold remains within common linear solid mechanics: a fully specified
100 mm x 10 mm x 10 mm elastic bar, one Static General step, 80 C3D8R elements,
minimal rigid-body constraints, and prescribed axial displacement. It requires
zero clarification and uses an independent ODB oracle.

The licensed reference/oracle integration passed with 80 C3D8R elements, 189
nodes, axis spans of 100/10/10 mm, coordinate-layer counts of 21/3/3, right-face
mean U1 of 0.1 mm, mean S11 of 210 MPa, left-face RF1 of -21000 N, and Y/Z
contractions of -0.003 mm.

The historical 3D Concept experiment used the immutable final7 Catalog shown
below. Keep this path when reproducing that frozen comparison; new runs and the
current commands above should use final15.

```powershell
uv run --group eval python -m abaqus_sim_agent.evaluation.bar_tension_3d `
  --run-root D:\abaqus-runs\bar3d-automatic `
  --model-provider opencode-go `
  --api-catalog-mode automatic_verified_only `
  --api-catalog-dir var\api-catalog\abaqus-2024-final7
```

Add the optional official-document Concept tool as a separate treatment:

```powershell
uv run --group eval python -m abaqus_sim_agent.evaluation.bar_tension_3d `
  --run-root D:\abaqus-runs\bar3d-concept `
  --model-provider opencode-go `
  --api-catalog-mode automatic_verified_only `
  --api-catalog-dir var\api-catalog\abaqus-2024-final7 `
  --concept-retrieval-mode tool `
  --concept-index-dir var\rag
```

The runner records the exact Catalog cards digest, Concept manifest digest,
gateway, model, prompt version, and usage metrics. The licensed experiment
observed:

| Arm | Closed-loop pass | Concept adoption | Important failure evidence |
|---|---:|---:|---|
| automatic verified-note control | 2/3 | 0/3 | one gateway timeout before any Abaqus call |
| Concept tool exposed before the preflight policy | 3/3 | 0/3 | none; zero lookups means the result is not attributable to RAG |
| `concept-tool-from-scratch-preflight-v1` | 2/3 | 3/3 | one `model_budget` failure at 40 model requests, 12 CAE calls, and 0 Solver calls |

Both the control and prompted preflight arm passed the 2/3 smoke gate, so this
task showed no closed-loop success benefit from Concept retrieval. The explicit
tool mode remains opt-in. Its prompt-only policy asks for exactly one pre-CAE
query on the highest-risk modeling relationship for a clearly from-scratch task;
a second query is reserved for genuine conceptual uncertainty after a CAE or
Solver failure. There is no host-side task classifier or execution blocker, and
Exact API Catalog/runtime notes remain the separate mechanism for API binding
failures. The frozen report is
[bar_tension_3d_concept_evaluation_20260824.json](bar_tension_3d_concept_evaluation_20260824.json).
Each arm still contains only
three stochastic attempts of one simple 3D task, not a stable success-rate
estimate.

The historical r2 final8 rerun passed `1/3` and failed the product gate. Its only
transport-successful attempt passed the hidden oracle in 9 model requests,
12 tool calls, 3 Abaqus calls, and 1 Solver call (`task pass | transport = 1/1`).
The preceding diagnostic run was also `1/3`: one Harness `os.killpg` cleanup
failure, one Go timeout, and one oracle pass at 27 model/10 Abaqus calls.
The dedicated Windows timeout regression verifies the Shell patch mechanically;
the final three Product-Agent attempts recorded the patch and no killpg error, but
made zero generic Shell calls, so they did not re-exercise that cleanup path.

The fresh `bar3d-baseline-final8-go-r3-45m-20260824-v3` root passed `2/3` and the
current 3D Gold gate (`task pass | transport = 2/2`). Attempt 1 timed out at the
Go endpoint before files or Abaqus calls after about 484.258 seconds. Attempt 2
passed in 19 model requests, 20 tool calls, 5 Abaqus calls, and 1 Solver call;
it repaired two ODB extraction errors and encountered one legitimate same-turn
expected-hash conflict. Attempt 3 passed in 29/32/9/2 model/tool/Abaqus/Solver
calls after four CAE failures and a missing-section Solver failure were repaired.

Compared with historical r2, retry=3 extended the terminal timeout by about
122 seconds but did not recover that request, and neither successful run is known
to have needed the extra retry. Every attempt finished below 1800 seconds, so the
45-minute ceiling never bound. The configuration therefore passes this 3D Gold
smoke without establishing a causal retry/wall-time benefit or the broader
Baseline-v1. No eval retry or repeat-until-pass was used.
Frozen evidence: [bar_tension_3d_final8_baseline_20260824.json](bar_tension_3d_final8_baseline_20260824.json).

## B31 axial beam Gold

The deterministic B31 reference, solver, extractor, and hidden oracle passed the
licensed Abaqus 2024 integration. The fixed model has 20 B31 elements and 21
nodes, right U1 of 0.1 mm, left/right RF1 of -21000/+21000 N, and one positive
SF1=21000 N integration-point value per element. The verifier also checks the
material, 10×10 mm profile, Static/NLGEOM definition, single axial connectivity
chain, section/orientation coverage, signed force balance, and narrow INP
boundary/load contract.

The initial Product-Agent root
`D:/abaqus-runs/beam-b31-baseline-final8-go-r3-45m-20260824-v1` observed `0/3`.
All three attempts ended with `ModelAPIError: Request timed out` before creating
any file or making any Abaqus call. A time-separated v2 rerun reached a complete
physical solution once, but exposed an evaluator bug: the task did not prescribe
beam-section integration mode, while the verifier silently accepted only
`DURING_ANALYSIS` and rejected the official `BEFORE_ANALYSIS` alternative. The
stored v2 oracle evidence passes after correcting that overconstraint, giving a
posthoc `1/3`, still below gate.

The corrected-verifier v3 treatment ran once and observed `1/3`: one attempt
passed all artifact, model, geometry, topology and physics checks in 10 model
requests, 4 Abaqus calls and 1 Solver call; one failed on a Go HTTP 500 before
side effects; one completed CAE and Solver but used all 12 Abaqus calls on API/
ODB probes and extraction repair before producing `results.json`. These runs
prove single-run B31 capability but not the required repeated reliability. No
batch used eval task retry or repeat-until-pass. Frozen evidence:
[beam_tension_b31_evaluation_20260824.json](beam_tension_b31_evaluation_20260824.json).

The requested full-Catalog v4 ablation changed only `api_catalog_mode` and also
observed `1/3`. Both task-started attempts used all four active lookups; one
passed in 22 model requests and 8 Abaqus calls, while the other used 39/12 and
exhausted the runtime budget after Solver. The third attempt timed out before
side effects. Across the two task-started attempts per arm, full and automatic
both passed once; full used 61 versus 42 model requests, 967,709 versus 474,375
input tokens, and 20 versus 16 Abaqus calls. The attempts were sequential and
unseeded, so this is descriptive evidence of no observed B31 benefit, not proof
that full Catalog generally causes the additional work. Catalog lookup confirmed
`Model.BeamSection` and several authoring methods but had no exact cards for
`MeshNodeArray.findAt`, `OdbMeshNodeArray.findAt`, or `OdbInstance.nodeSets`.

The subsequent `automatic_exact_signature` v5 treatment also observed `1/3`.
Its only task-started attempt passed but triggered no API traceback or lookup;
the other two attempts timed out before files. The batch therefore confirms
non-interference but is inconclusive for B31 task benefit. The separate
BeamSection regression above directly verifies the no-note signature path.

The current full-final9/no-Shell v6 batch kept the B31 prompt, Skill,
`40/4/12/3` budget, verifier, repeat, and no-retry policy fixed and first passed
the gate at `2/3`. One attempt timed out before files; both task-started attempts
passed every hidden check. They used 28/10 and 12/3 model/Abaqus calls, so the
gate does not hide the remaining convergence-cost tail. The two passes used
eight full method cards; `Method.parameter` adoption was `0/2`, and one attempt
received the verified `seedEdgeByNumber` container note. This is valid B31 gate
evidence for the frozen v6 configuration, not a causal full-Catalog/no-Shell
uplift or cross-family parameter-lookup result.

## Verified-seed B31 section change

The B31 section-change evaluator revalidates one completed B31 Gold request,
outcome, verification, zero-clarification contract, and six artifact hashes,
then seeds only its two Python scripts. The request changes the rectangular
profile from 10×10 to 20×10 mm while preserving the extractor byte-for-byte and
all geometry, mesh, orientation, material, step, BC, output, and stage contracts.
The 42 kN axial result is owned by the host oracle and is not included in the
Agent prompt.

The licensed deterministic reference passed `1/1`. The formal OpenCode Go /
DeepSeek V4 Flash batch ran with Catalog, Concept, and Planning off and passed
`3/3`; every attempt used exactly three Abaqus calls and one Solver, produced a
non-empty build patch and zero-byte extractor patch, and independently measured
20×10 mm, U1≈0.1 mm, and RF1/SF1=±42 kN. This is one linear axial requirement
change from one verified source, not arbitrary beam or generic CAE editing.
Frozen evidence:
[beam_section_change_evaluation_20260827.json](beam_section_change_evaluation_20260827.json).

## Verified-seed B31 bending and result-contract change

The second B31 change uses the same independently verified axial source, but it
is orthogonal to the profile edit. It removes the right-end U1 displacement,
adds one 1000 N global +Y concentrated load, and requires both source scripts to
change because the consumer contract becomes `right_mean_u2`, `left_sum_rf2`,
`left_sum_rm3`, and `max_section_moment`. The expected physical values are
host-owned and absent from the Agent prompt.

The strict host path parses the INP for one left Encastre, one right DOF2 CLOAD,
and no other load; checks the current three-stage provenance; and independently
reads U/RF/RM/SF/SM from the ODB. The licensed deterministic reference passed
`1/1` with U2=1.919047594 mm, RF2=-1000 N, RM3=-100000 N·mm, maximum
integration-point section moment 97500 N·mm, and near-zero axial response.

The initial OpenCode Go / DeepSeek V4 Flash v1 batch kept Catalog, Concept, and
Planning off and the general Shell disabled. It passed `3/3`; every attempt had
non-empty build and extractor patches, one Solver call, current artifacts, and
matching `results.json`/host ODB measurements. The attempts used 8, 9, and 6
Abaqus calls, respectively, versus the three-stage minimum. Frozen evidence:
[beam_bending_change_evaluation_20260827.json](beam_bending_change_evaluation_20260827.json).

### Bending convergence and contract disclosure

All three v1 traces first failed because they searched for RM3 inside RF and
would have treated SM as part of SF. Their first probes also sliced a vendor
`FieldValue` sequence, which the licensed container rejects; two traces then hit
vendor-value JSON serialization errors. A generic production-first treatment
kept the first production extractor ahead of probes but still used `7/7/8`
Abaqus calls and repeated the ownership error `3/3`.

A read-side RF/RM and SF/SM card produced one ideal three-call trace, but the
other two traces added RM to the CAE output request and used `9/10` calls. This
also exposed a verifier hole: the INP check accepted any output set containing
U/RF/SF. After changing it to exact equality, those two artifacts fail and that
arm replays as `1/3`, not its original `3/3` report.

The recommended `declared_odb_contract_v2` request makes the previously hidden
consumer contract explicit without providing expected numeric results: CAE
authoring remains exactly U/RF/SF; RF/RM and SF/SM are separate companion ODB
fields; and `max_section_moment` means the maximum SM vector magnitude. With the
same source, model, Skill, budgets, retrieval-off state, and host physics oracle,
the three runs passed both the original gate and corrected strict replay. Each
used exactly one CAE, one Solver, and one production extractor; no diagnostic
script was created. Aggregate Abaqus calls fell from 23 to 9 and model requests
from 74 to 35. This is a three-run, one-task contract-disclosure result, not a
general prompting or arbitrary-beam claim. Frozen evidence:
[beam_bending_convergence_evaluation_20260827.json](beam_bending_convergence_evaluation_20260827.json).

## Verified-seed plate model-change suite

L1 contains three orthogonal changes from the same independently verified
`plate-tension-v4` source:

| Case | Change axis | Current Product-Agent result |
|---|---|---:|
| `center_hole` | geometry and mesh topology; host-owned new metrics | v2 **1/3**, gate not passed; task-complete 1/1 |
| `dual_material` | partition, section assignment, conforming interface | **2/3, gate passed** |
| `load_mode` | prescribed displacement to uniform general traction | corrected off 0/3; full Catalog **2/3, gate passed** |

The source loader verifies request, outcome, plate verification, clarification,
and six artifact hashes, then caches the two script byte sequences. Every
attempt starts from those cached bytes; old CAE/INP/ODB/results and private
oracles are not copied. Common runtime code owns Agent/Service assembly,
snapshots, byte-faithful patches, three-stage execution provenance, and terminal
verification. Hole, material, and load physics remain in three independent
strict verifiers. In center-hole v2 the original extractor must stay unchanged;
the host oracle alone computes hole topology, discretization, and stress
concentration.

Run all cases, or select one with repeated `--case-id`:

```powershell
uv run --group eval python -m abaqus_sim_agent.evaluation.model_change_suite `
  --run-root D:\abaqus-runs\plate-model-change-suite `
  --source-run-dir D:\abaqus-runs\plate-eval\<completed-run-id> `
  --model-provider opencode-go

uv run --group eval python -m abaqus_sim_agent.evaluation.model_change_suite `
  --run-root D:\abaqus-runs\plate-material-only `
  --source-run-dir D:\abaqus-runs\plate-eval\<completed-run-id> `
  --case-id dual_material
```

Each selected Case has its own 2/3 gate; total passes cannot offset a failed
Case. All three deterministic Abaqus 2024 references pass. The dual-material
formal batch passed 2/3, with one pre-task Go timeout and both task-started
attempts passing the hidden oracle.

The first load suite contained one complete correct Agent solution that was
rejected because the INP surface `SurfRight` was compared case-sensitively
against ODB `SURFRIGHT`. The original raw result remains 0/3. After fixing the
host overconstraint, the Agent script passed in a fresh replay; a new load-only
three-repeat batch still observed 0/3, so load-mode is not a stable capability.
No Concept tool, Skill recipe, eval retry, or repeat-until-pass was added. A
subsequent bounded full-Catalog treatment kept the load goal, verified source,
Skill, model, runtime/verifier, and Abaqus/Solver budgets fixed. It passed 2/3;
both passing attempts found `Model.SurfaceTraction` and `Assembly.Surface`,
while the failed attempt still used 36 model requests and 10 Abaqus probes
without reaching Solver. This supports a benefit on the frozen load task, not a
universal Catalog claim.

A budget-only follow-up exposed `--model-request-budget`,
`--abaqus-call-budget`, and `--solver-call-budget`, then changed only the full
Catalog limits from 36/12/3 to 48/18/3. The result remained 2/3. One passing
trajectory needed 44 model requests, so the old request cap would have excluded
that legitimate long-tail completion. Another attempt used all 48 model
requests and 16 Abaqus probes without reaching Solver. Aggregate model requests
rose from 68 to 122 and Abaqus calls from 17 to 36 without a gate improvement.
Defaults therefore remain 36/12/3; the expanded profile is explicit evidence
that more budget can admit useful exploration but cannot enforce convergence.

The opt-in typed-probe protocol separates diagnostic runtime success from a
validated claim. Diagnostic calls must record a strict conclusion before any
later Abaqus runtime action; a validated result can target only the Case-owned
production scripts, whose hashes are frozen when the diagnostic completes.
Until the target changes and runs as production, further diagnostics, Solver,
and verification are blocked. Conclusions may be attached atomically to the
next runtime call and are appended with adoption events to
`state/probe_results.jsonl`; they are not promoted into Skill or Catalog.

The first Product-Agent protocol version was 0/3 and is retained as rejected
implementation evidence: it allowed a new probe file to masquerade as the
production target and added one model round-trip per standalone conclusion.
`typed-probe-convergence-v2` added per-Case target allowlists, diagnostic-time
target hashes, and atomic conclusions. Under the same full-Catalog 48/18/3
configuration it passed 2/3, equal to the protocol-off high-budget control. The
two passes used direct production paths with no probe; the failed attempt wrote
seven inconclusive conclusions and never validated a candidate. This verifies
state/persistence and normal-path non-interference, not a convergence benefit.

The repeated gap was then promoted manually—not automatically—from a separate
licensed micro-regression. With all other arguments fixed,
`Assembly.Set(edges=...)` was rejected by the Abaqus 2024 SurfaceTraction
consumer, while `Assembly.Surface(side1Edges=...)` created the load. The new
final9 artifact keeps 5,481 cards and 48 conflicts, adds one verified note
scoped only to `Model.SurfaceTraction.region`, has cards SHA-256
`3e3294808dc180df2d13ce5183095ebb6ecc47998f6313c96398af25c564255b`, and
passes 13/13 deterministic queries. In the same typed-v2 48/18/3 Product batch,
the note was delivered 3/3 and all three production scripts used
`Assembly.Surface`; two task-complete attempts passed, while one later hit an
independent directionVector error and then a `run_command` infrastructure
failure. The gate remained 2/3, so this is a region-binding adoption result,
not a stable-rate improvement claim.

Center-hole v2 passed its licensed deterministic reference and one Product-Agent
attempt without changing the extractor. The hidden verifier first rejected a
real regression: partitioning split both outer edges at y=10, while the model
constrained only one segment. The Agent selected both segments, reran all three
stages, and passed. Two other attempts timed out at OpenCode Go before any
Abaqus call, so the formal result remains 1/3 and below gate.

Frozen evidence:

- [historical suite](plate_model_change_suite_evaluation_20260825.json)
- [bounded treatments](plate_model_change_treatments_20260825.json)
