# Foundation Skill Public Mechanics Benchmark v1

This benchmark measures whether exposing the frozen linear-structural FEA Foundation Skills improves engineering behavior relative to the same Agent with those Skills hidden. It complements the existing handcrafted diagnostic set with independently checkable public/classic mechanics problems.

## Frozen public/classic set

The six cases are:

1. uniform axial bar;
2. two-segment stepped axial bar;
3. cantilever with a tip point load;
4. cantilever with a full-span uniform line load;
5. finite rectangular plate with a circular hole in tension, based on Ansys Workbench VM-WB-MECH-002;
6. 3D solid cantilever directional loading, based on Ansys VM184.

The first four cases use public textbook mechanics relations and repository-owned parameters. The last two bind public vendor verification targets without importing vendor APIs, project files, figures, element-name requirements or product-specific solution procedures. Source metadata and scope boundaries are frozen in `foundation_public_mechanics_sources_v1.json`.

Reference equations, numeric answers and rubric criteria are host evidence. A future evaluator must send only each case `goal` and `seed_files` to the model. It must not place the hidden `reference`, source notes or rubric in the Agent context.

## Treatment profiles

Use the same Agent assembly, model/provider, task instructions, model-request budget, wall budget, workspace, clarification behavior and zero-runtime constraints for each matched pair. The only treatment difference is:

- `control`: `foundation_skill_profile="off"`;
- `foundation`: `foundation_skill_profile="linear_structural_fea_v1"`.

Keep Catalog, Concept/document retrieval, diagnostic advisory, Planning, Abaqus runtime and Solver runtime off. Do not edit the Foundation Skills between matched control/foundation runs or between pilot repeats.

## Behavior scoring

Each trial receives the frozen case rubric and a host semantic review. Provider timeout, wall timeout, evaluator failure or missing trace is `unknown`/measurement-invalid rather than an engineering fail. Skill routing and whether the model happened to load a particular capability are descriptive evidence only.

For each valid matched case/repetition pair:

- `win`: control fails and Foundation passes;
- `tie`: both pass or both fail;
- `loss`: control passes and Foundation fails;
- `unpaired_unknown`: either side is not measurement-valid or has an unknown final behavior outcome.

Do not collapse `fail/fail` into a treatment win merely because one answer looks somewhat better. Record severity separately.

## Engineering error severity

Every semantically reviewed trial also records the highest observed engineering-error severity:

- `S0`: no material engineering error, or only wording/presentation omission that does not change model meaning, numerical reference, discretization choice or result interpretation;
- `S1`: minor engineering judgment error with limited consequence for this case;
- `S2`: error that changes modeling, loading/support meaning, discretization reasoning, QoI/result semantics or a material numeric reference;
- `S3`: wrong physical model or conclusion that would invalidate the requested engineering decision.

Severity does not replace pass/fail. It answers whether the treatment reduces consequential mistakes even when both profiles still fail a strict rubric. Report total and per-case S2/S3 counts for each profile.

## Combined diagnostic set

Keep the existing frozen diagnostic cases as a separate stratum. For the new Foundation-effect comparison use these six:

- `existing-property-load-edit`;
- `element-mesh-review`;
- `stress-result-semantics`;
- `completed-result-credibility`;
- `local-name-mismatch`;
- `missing-user-physical-input`.

Exclude `pure-abaqus-api-repair` from the main Foundation benchmark because it primarily measures release-specific API/Catalog behavior and has repeatedly timed out. Do not rewrite the historical r1/r2 diagnostic rubric or results. `from-scratch-linear-plate` remains historical diagnostic evidence; the public mechanics set now supplies cleaner from-scratch mechanics checks with numeric references.

## Pilot before full repetition

Freeze this v1 specification before any treatment run. The first pilot is 6 cases x 2 profiles x 2 repeats = 24 trials:

- public: `uniform-axial-bar`, `cantilever-tip-load`, `plate-with-hole-tension`;
- diagnostic: `existing-property-load-edit`, `stress-result-semantics`, `missing-user-physical-input`.

If the pilot shows no paired treatment improvement, do not automatically spend the full 3-repeat suite. A useful positive signal is more valid wins than losses without an increase in S2/S3 errors; because the sample is deliberately small, do not turn that heuristic into a universal statistical threshold or claim a stable success rate.

If the pilot is promising, expand to all 12 public+diagnostic cases with three isolated repeats per profile. Report paired wins/ties/losses, unpaired unknowns, per-profile S2/S3 counts, and model-request/input-token/output-token/wall-time cost. The intended eventual claim format is comparative, for example: "Foundation produced X paired wins and Y losses and changed S2/S3 error count from A to B at C% token cost," not "Foundation is N% correct."

## Change policy

Do not patch the Skills for a single benchmark miss. First collect repeated cross-case failure patterns. Any change to a task, hidden reference, rubric, source boundary, Skill body, Agent assembly, model profile or task instructions creates a new experimental binding rather than rewriting v1 evidence.
