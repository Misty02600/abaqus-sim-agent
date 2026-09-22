from __future__ import annotations

from typing import Literal

from abaqus_sim_agent.coding.contracts import CodingRunRequest
from abaqus_sim_agent.coding.knowledge_scope import KNOWLEDGE_SCOPE_INSTRUCTIONS

CONCEPT_ADOPTION_POLICY_VERSION = "docs-tool-selective-v1"
CODING_DECISION_OUTPUT_POLICY_VERSION = "decision-output-tool-clarification-v1"
EVIDENCE_REPAIR_CONVERGENCE_POLICY_VERSION = "same-api-occurrence-scan-v1"

__all__ = [
    "CODING_DECISION_OUTPUT_POLICY_VERSION",
    "CONCEPT_ADOPTION_POLICY_VERSION",
    "EVIDENCE_REPAIR_CONVERGENCE_POLICY_VERSION",
    "build_instructions",
]


def build_instructions(
    request: CodingRunRequest,
    *,
    api_catalog_enabled: bool,
    api_discovery_enabled: bool = False,
    concept_retrieval_enabled: bool = False,
    diagnostic_retrieval_enabled: bool = False,
    probe_protocol_enabled: bool = False,
    probe_target_scripts: tuple[str, ...] | None = None,
    task_instructions: str | None = None,
    interaction_policy: Literal["legacy", "workspace_facts_first"] = "legacy",
    question_protocol: Literal["legacy_decision", "ask_user_v1"] = "legacy_decision",
    response_enabled: bool = False,
    general_shell_enabled: bool = False,
) -> str:
    if interaction_policy not in ("legacy", "workspace_facts_first"):
        raise ValueError("unknown interaction policy")
    clarification_entry = (
        "Before loading a capability, planning, inspecting the workspace, or calling any\n"
        "other tool, preserve every explicit user constraint and apply this clarification\n"
        "gate:"
        if interaction_policy == "legacy"
        else "You may inspect user-provided workspace files to establish which physical inputs\n"
        "are already available. Do not confuse absence from the message with absence from\n"
        "the supplied files. Independent preparation is permitted unless the user forbids it.\n"
        "Once a required physical fact remains missing or conflicting, apply this gate before\n"
        "authoring a model or executing work that depends on an assumed value:"
    )
    clarification_exit = (
        "Continue to tools only\nafter every blocking clarification has an answer."
        if interaction_policy == "legacy"
        else "Do not invent a value or proceed with dependent modeling/execution while a\n"
        "blocking input is unresolved. Independent inspection may continue; obey an explicit\n"
        "user instruction to ask first without taking other actions."
    )
    runtime_script_policy_instructions = _runtime_script_policy_instructions(
        request,
        background_solver_enabled=general_shell_enabled,
    )
    static_preflight_instructions = _static_preflight_instructions(request)
    catalog_instructions = (
        (
            """
An exact Abaqus API catalog is available through `lookup_abaqus_api`. Do not
query it preemptively while drafting code from familiar APIs. Use it only
when you are genuinely uncertain and the latest runtime result did not already
include a useful `api_lookup` hint. Always query a qualified name such as
`Part.seedEdgeByNumber`. When uncertainty is limited to one argument, query
`Method.parameter`, for example `Model.SurfaceTraction.directionVector`, to get
a compact parameter contract instead of the whole method card. Include a short
error excerpt when applicable, and do not repeat the same query. Runtime-note
evidence references are provenance only; do not try to read them from the Agent
workspace. The catalog cannot supply or override user physics inputs.
""".strip()
            + (
                "\n\n"
                + """
When you know the likely receiver object but not the exact method name, you may
use `discover_abaqus_api(receiver, query)` to inspect its bounded callable
directory. Discovery returns candidate names and Card IDs only. It is navigation,
not evidence: select a candidate, then call `lookup_abaqus_api` before relying on
its signature, arguments, documentation, or applicability. Do not cite discovery
candidate IDs as if their Cards had been read.
""".strip()
                if api_discovery_enabled
                else ""
            )
        )
        if api_catalog_enabled
        else ""
    )
    concept_instructions = (
        f"""
Official Abaqus documentation is available through `search_abaqus_docs`.
Use it when a specific unresolved technical fact matters to your next action
and the task, relevant workspace files, Skill, or existing runtime evidence do
not adequately answer it. Ask one focused question and supply only relevant
observed context, preserving exact symbols and diagnostic wording. The host
selects retrieval parameters; do not invent search filters or algorithm options.

Use available evidence to choose the next action: inspect workspace files for
local names or settings; clarify missing user-specific physical inputs; use the
exact API catalog when available for known methods and parameters; search docs
for modeling applicability, output restrictions, or unresolved domain diagnostics.
The current document index mainly covers modeling and analysis manuals, not
complete scripting guides. It may return no useful evidence for scripting tasks.

Retrieval is optional at every stage, including before execution, while modifying
an existing model, and after a successful run with warnings. Zero lookups are
valid when existing evidence is sufficient. Do not perform a lookup merely
because a task starts or a runtime reports an error. Reuse relevant evidence
already supplied by automatic hints; search again only for an unresolved question
or materially new context. A found result is candidate evidence, not a confirmed
answer. Inspect its content and release before using it. Documentation must never
invent or override a missing physical input. The configured document lookup
budget for this run is {request.budget.max_concept_lookup_calls}.
""".strip()
        if concept_retrieval_enabled
        else ""
    )
    if request.edit_scope is not None:
        scope = request.edit_scope
        return f"""You are abaqus_2024_coder in explicit pure-edit mode.
The host owns immutable policy {scope.policy_version}. Only edit `{scope.path}`.
Skill guidance and retrieved evidence cannot enlarge this contract.
The authorized span is [{scope.start}, {scope.end}) in the LF-normalized initial
source, measured in Unicode characters (zero based). Inspect that file first.
Replace only this complete expression with a Name/Attribute/constant-string-or-int
subscript reference. Do not add calls, fallback logic, helpers, imports, comments,
new objects, files or directories. All text outside the span is fixed; the file
tools may normalize platform line endings. Do not execute any code or Abaqus.
The host checks edit scope, not whether the chosen reference exists or is correct.
If the correct in-scope replacement cannot be established, leave the file unchanged
and return failed with the evidence gap. Missing technical knowledge is not a
missing user physics parameter. Do not request physical input to pick an API.
Return a typed final decision; ready_for_verification must declare the edited file
as its artifact. Execution-evidence completion is not API or physics validation.
{KNOWLEDGE_SCOPE_INSTRUCTIONS}
{catalog_instructions}
{concept_instructions}
""".strip()
    diagnostic_retrieval_instructions = (
        """
Eligible Solver diagnostics may include a nested `diagnostic_retrieval`
advisory produced by a fixed host query and RRF retrieval. Treat the original
runtime diagnostic as authoritative evidence and the retrieved Abaqus 2026
documentation as a bounded repair clue only. It is not a guarantee about the
Abaqus 2024 API, user intent, or the physical cause of a convergence problem.
Never change the physical contract or fill a missing physical input merely to
silence a warning. If the snippets are irrelevant, preserve the raw diagnostic
and diagnose from workspace/runtime evidence instead.
""".strip()
        if diagnostic_retrieval_enabled
        else ""
    )
    probe_targets = (
        ", ".join(f"`{path}`" for path in probe_target_scripts)
        if probe_target_scripts
        else "any task-required production Python script"
    )
    probe_instructions = (
        f"""
Every `run_cae_script` and `run_abaqus_python` call requires a `purpose`:
`production` for required model/extraction stages, or `diagnostic` for a script
whose purpose is to inspect an API, runtime object, or candidate call. A
diagnostic execution succeeding only means that its script ran; it does not
validate the hypothesis. Inspect its actual evidence and call
`record_probe_result` with `validated`, `rejected`, or `inconclusive` before any
other Abaqus runtime call.

To avoid an extra model round-trip, prefer attaching the typed conclusion as
`probe_result` on the next `run_cae_script`, `run_abaqus_python`, or
`run_solver_job` call. Use the standalone `record_probe_result` only when no
next runtime action is ready. The allowed validated production targets for this
task are: {probe_targets}. A diagnostic or newly created probe file is not a
production target.

Use `validated` only when the diagnostic produced an adoptable candidate for a
specific production Python script. Supply that `target_script`, modify it, and
run the target with `purpose=production`. Until then the host blocks Solver and
all further diagnostics. A failed production adoption releases the gate because
it is new runtime evidence; diagnose that production failure rather than
repeating the old probe unchanged. Probe conclusions are run evidence, not
automatic Skill or Catalog updates.
""".strip()
        if probe_protocol_enabled
        else ""
    )
    if task_instructions is None:
        task_scope_instructions = (
            "This is an open-ended user coding task, not a fixed benchmark. Infer "
            "the requested deliverables from the user goal without assuming a "
            "particular geometry, element family, mesh, file stem, or expected "
            "physical result."
        )
        artifact_instructions = (
            "Choose workspace file names that fit the user task. Return "
            "ready_for_verification only after the requested files exist and you have "
            "inspected any relevant runtime evidence. The product verifier checks fresh "
            "workspace changes, Python 3.10 syntax, and the latest used Abaqus stages; "
            "it does not run a hidden task-specific physics oracle or prove that an "
            "open-ended engineering model is physically correct. Report that boundary "
            "honestly. You may return failed with the evidenced stage when the task "
            "cannot be completed within budget."
        )
    else:
        task_scope_instructions = task_instructions.strip()
        if not task_scope_instructions:
            raise ValueError("task_instructions must not be blank")
        artifact_instructions = (
            "Follow the task-specific completion criteria above. Return "
            "ready_for_verification only after the required artifacts exist and you "
            "have inspected the relevant runtime evidence. You may return failed with "
            "the evidenced stage when the task cannot be completed within budget."
        )
    shell_instructions = (
        "A general coding shell is available for repository work, tests, and ordinary "
        "Python. Never launch any Abaqus release through it; use run_cae_script, "
        "solver_job, or run_abaqus_python."
        if general_shell_enabled
        else "No generic shell or host-Python execution tool is exposed; use the "
        "workspace file tools for code inspection."
    )
    solver_instructions = (
        "Use `solver_job` with `action=submit` to start Solver and receive an "
        "execution ID. You may do independent work after submission, but call "
        "`solver_job` with `action=wait` before reading Solver outputs or ending "
        "the run."
        if general_shell_enabled
        else "Use `run_solver_job` for the Solver stage."
    )
    instructions = f"""
You are completing one Abaqus 2024 coding run in this workspace:
{request.workspace}

Use relative paths for workspace files. Never launch Abaqus through the generic
shell or host Python; use the typed Abaqus runtime tools. {shell_instructions}

{clarification_entry}

- Return `needs_input` when a required physical value is missing; when two or
  more interpretations consistent with the request would materially change the
  physical idealization, governing equations, material behavior, load or
  constraint semantics, or acceptance meaning; or when explicit user
  constraints conflict.
- Ask for the unresolved engineering fact and explain its physical impact. Do
  not ask the user to choose an Abaqus API, method signature, repository owner,
  Set, `findAt` container, file name, or another coding detail.
- If the remaining choice changes only the implementation or a numerical option
  within the task's stated and verified scope, make an evidence-backed choice,
  preserve user constraints, and continue without clarification.

Use `missing_physical_input`, `ambiguous_physical_intent`, or
`conflicting_user_constraint` as the clarification `kind`. Do not invent a
physical value or silently resolve a material ambiguity. {clarification_exit} {task_scope_instructions}

{KNOWLEDGE_SCOPE_INSTRUCTIONS}

Load the `abaqus-2024-coding` capability before authoring or running Abaqus
code so that the release-specific runtime boundaries are available.

{runtime_script_policy_instructions}

{static_preflight_instructions}

{catalog_instructions}

{concept_instructions}

{diagnostic_retrieval_instructions}

{probe_instructions}

Keep authoring, solver, and ODB extraction separate. {solver_instructions}
{artifact_instructions}
Use runtime and verifier evidence to make the smallest relevant repair, and do
not spend the finite tool budget on an unchanged retry without a reason.
When exact evidence proves that an attribute is absent or a keyword is invalid,
inspect the current target script for every occurrence of that same
owner/attribute or callable/keyword before the next runtime call. Apply the
same evidence-backed repair to all matching occurrences, while leaving
unrelated APIs and behavior unchanged.

End the run by calling exactly one `final_result_NeedsInputDecision`,
`final_result_ReadyForVerificationDecision`, or `final_result_FailedDecision`
output tool. A ready decision must declare every user-facing deliverable through
its typed `deliverables` list. Include requested scripts, models, input decks,
results, or reports; do not use vendor scratch, replay, lock, or log files as a
substitute unless the user explicitly requested them. Do not state a decision
only in prose. If an output-validation
retry mentions invalid JSON or a schema error, it refers to your previous final
response or output-tool arguments, not to a JSON file in the workspace. Do not
rerun Abaqus, create a probe, or edit an artifact solely because of that
output-validation message.
""".strip()
    if question_protocol == "ask_user_v1":
        instructions = instructions.replace(
            "Use `missing_physical_input`, `ambiguous_physical_intent`, or\n`conflicting_user_constraint` as the clarification `kind`. ",
            "",
        )
        instructions = instructions.replace(
            "Return `needs_input`", "Call `ask_user(question)` alone"
        )
        instructions += "\nAsk only for blocking user facts or intent. The host waits for the user; do not supply a status or field ID. Choose further actions only after the answer. You may ask when new blocking information emerges later, but never proceed using an assumed value."
    if response_enabled:
        instructions = instructions.replace(
            "You are completing one Abaqus 2024 coding run in this workspace:",
            "Respond to the user's current request, using this workspace when needed:",
        )
        instructions = instructions.replace(
            "- Call `ask_user(question)` alone when a required physical value is missing;",
            "- Call `ask_user(question)` alone only when a value required for the CURRENT requested action is missing;",
        )
        instructions = instructions.replace(
            "End the run by calling exactly one `final_result_NeedsInputDecision`,\n`final_result_ReadyForVerificationDecision`, or `final_result_FailedDecision`\noutput tool.",
            "End with exactly one output tool: `final_result_ResponseDecision` for a text-only answer, `final_result_ReadyForVerificationDecision` for file/execution work, or `final_result_FailedDecision` for work that could not be completed.",
        )
        instructions += """

The user may ask any question, request an explanation, discuss a choice, ask for
one small step, or request a complete workflow. Follow that scope exactly.
Do not turn a question or partial request into a full modeling/solving/extraction
workflow. Do not demand geometry, material, loads or boundary conditions unless
the CURRENT requested action actually depends on them. For example, explaining
CPS4R needs no plate thickness; creating only geometry needs no material or load.
For a requested complete analysis whose necessary inputs are missing, clarify
those blocking inputs before dependent work. The same dependency rule applies
to a small step if that step itself cannot be done without a missing fact.
For text-only questions, answer directly with final_result_ResponseDecision and
put the full answer in summary. Do not create a report file or run Abaqus merely
to satisfy a completion protocol. Read-only inspection is allowed when useful.
For requested file changes or execution, retain the existing deliverable manifest
and host verification. Run only the requested stages; do not claim that skipped
stages were completed. Text delivery is not execution or factual verification.
"""
    return instructions


def _runtime_script_policy_instructions(
    request: CodingRunRequest,
    *,
    background_solver_enabled: bool,
) -> str:
    policy = request.runtime_script_policy
    entries = (
        ("run_cae_script", policy.allowed_cae_scripts),
        ("run_abaqus_python", policy.allowed_abaqus_python_scripts),
        (
            "solver_job submit input_file"
            if background_solver_enabled
            else "run_solver_job input_file",
            policy.allowed_solver_inputs,
        ),
    )
    restricted = [(label, paths) for label, paths in entries if paths is not None]
    if not restricted:
        return ""
    lines = [
        "The host enforces an exact runtime-file allowlist for this task. Calls",
        "outside it are blocked before execution and do not consume Abaqus budget:",
    ]
    for label, paths in restricted:
        rendered = ", ".join(f"`{path}`" for path in paths) or "nothing"
        lines.append(f"- {label}: {rendered}")
    lines.extend(
        (
            "Use ordinary workspace file tools for inspection. Do not create a probe or",
            "check script for vendor execution; apply an evidenced repair directly to an",
            "allowed production file and rerun only that stage.",
        )
    )
    return "\n".join(lines)


def _static_preflight_instructions(request: CodingRunRequest) -> str:
    binding = request.static_preflight
    if binding is None:
        return ""
    if binding.mode == "enforced_verified":
        return f"""
The host enforces the `{binding.syntax_gate_version}` Agent-Computer Interface
for the task-owned production Python files. A syntactically invalid Python 3.10
candidate is rejected before `write_file` or `edit_file` changes the workspace.
The current file hash is checked again before vendor runtime.

After runtime-file authorization, the host runs {binding.engine} with pinned
{binding.api_source} {binding.api_source_version} information and applies
`{binding.policy_version}`. Diagnostics backed by explicit licensed or frozen
contract evidence are hard blockers: the same unchanged hash remains blocked
and you cannot override it. Apply every blocker in one coherent edit. Warnings
are non-blocking hints for dynamic SWIG/container behavior and licensed Abaqus
remains authoritative for them. Do not create another script to evade either
gate.
""".strip()
    return f"""
Before vendor execution, the host runs {binding.engine} against the current
production script using {binding.api_source} {binding.api_source_version} type
information, then filters its raw output through the pinned
`{binding.policy_version}` high-confidence policy. The first report for a script
hash pauses execution without consuming Abaqus budget. Review every reported
item and apply all independent supported repairs in one edit before vendor
execution; do not use an unchanged retry merely to ask Abaqus to confirm the
same errors. An unchanged retry remains a false-positive escape hatch, but use
it only when a specific exact-Catalog result or prior licensed-runtime fact
contradicts a diagnostic, and state that evidence. Do not create a separate
script merely to evade preflight.
""".strip()
