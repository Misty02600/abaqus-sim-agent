---
name: abaqus-2024-coding
description: "Use only for Abaqus 2024-specific implementation or runtime evidence: exact Abaqus Python API or INP keyword semantics, CAE/solver/ODB execution, release-specific artifacts/errors, or code changes whose correctness depends on those vendor semantics. Do not load it for structural/FEA reasoning alone, even when the task uses Abaqus terminology, or for workspace-local edits whose replacement is fully established by local files and needs no Abaqus-specific knowledge."
---

# Abaqus 2024 Coding

Use the coding harness's ordinary file, search, and edit capabilities. Write real Abaqus Python or input-deck text; execute Abaqus only through the host runtime tools.

## Runtime invariants

- The current default runtime is Abaqus 2024 Established Products at `D:\SIMULIA\Commands\abq2024.bat`.
- Abaqus 2024 embeds CPython 3.10.5. Its scripts do not run in the host CPython 3.12 environment and must not depend on host packages such as Pydantic.
- Run model-building and CAE database operations through `cae noGUI`; run lightweight ODB extraction through `abaqus python` with `odbAccess`; use `viewer noGUI` only when Visualization/session behavior is needed.
- Use the user-provided working directory and keep Abaqus-generated files there. Prefer ASCII-only working paths and job names on this Windows host.
- Treat the host runner as the only Abaqus entrypoint. Do not invoke the versioned launcher through the generic shell.
- A CAE/Viewer noGUI process can report exit code 0 after a script error. Treat the expected `.cae`, `.inp`, `.odb`, or result file plus its stage log as the completion evidence; ordinary `print()` is not a reliable machine-result channel.
- Standalone Abaqus Python stdout may be GBK on this host. Machine-readable output belongs in UTF-8 JSON rather than stdout or non-ASCII command-line arguments.
- If the target release is not 2024, stop applying release-specific claims from this skill and select or create the matching runtime profile.

## Core object patterns

These are object-model rules, not a recipe for any particular model. `Official` means the Abaqus Scripting Reference; `2024 reference` means the behavior was also checked with the licensed Abaqus 2024 fixture/runtime.

- **Imports — Official + 2024 reference.** Start ordinary CAE authoring with `from abaqus import mdb`, `from abaqusConstants import *`, and `from caeModules import *`; focused scripts may instead explicitly `import mesh`, `import step`, or the other documented access module. Loading the relevant module makes its dynamic APIs available and initializes/registers its repositories, so these imports are not merely for type names.
- **ODB imports and numeric operations — Official import semantics + local Abaqus 2024 observation.** Prefer module-qualified calls such as `import odbAccess; odb = odbAccess.openOdb(path=..., readOnly=True)`, or import only needed names such as `from odbAccess import openOdb`. Do not infer missing imports: check the documented access module for each API. `from odbAccess import *` shadows Python's `sum` and `pow` and may overwrite other names such as `log`. For ordinary numeric summation, use `import builtins` and `builtins.sum(values)`. On this Windows Abaqus 2024 installation, the vendor `sum` raises a type error for numeric lists/tuples and can crash on `dict.values()` or generators; converting to a list is not a substitute for choosing the correct function. This is not an EVOL reading restriction or a blanket ban on all Abaqus wildcard imports.
- **Repositories — Official.** `mdb.models`, `model.parts`, `model.steps`, `model.loads`, and similar repositories are dictionary-like: inspect keys and retrieve existing objects by key. Only the corresponding constructor adds an object to a repository; do not treat assignment to the mapping as object creation.
- **Geometry selection — Official syntax + 2024 runtime.** For `findAt`, a single point is a coordinate triple such as `findAt((x, y, z))`; a multi-point call uses one nested positional argument per point, such as `findAt(((x1, y1, z1),), ((x2, y2, z2),))`. Even a 2D part uses three coordinate components. Prefer passing the returned native Abaqus sequence directly, but treat conversion and nesting as consumer-specific: a Python list/tuple is not universally interchangeable with the vendor sequence. Query the exact API card before using `FaceArray.findAt` with normals or converting a selection for another API.
- **Regions — Official + 2024 reference.** Create a persistent named part `Set` and pass that `Set` to `SectionAssignment`. For a boundary condition or load applied after instancing, create an assembly-level `Set` from the target instance's geometry and pass that instance-level region; preserve the part/assembly ownership level instead of mixing repositories.
- **ODB fields — Official.** Use `FieldOutput.componentLabels` to interpret component order and `FieldValue.data` plus `FieldValue.nodeLabel` to read values and locations. Prefer `FieldOutput.getSubset(...)` by region, position, section point, or instance before iterating `.values` instead of scanning the whole field and guessing component indices.
- **Evidence-driven repair — 2024 trace evidence + project policy.** When a traceback or `.dat` diagnostic already identifies a failing call, do not dump every attribute with `dir()`. Apply one minimal evidence-backed repair, rerun only the failed stage, and reassess the new evidence before changing anything else.
- **Scripts and tools — Project runtime contract, not a vendor-document claim.** The Agent still writes ordinary Abaqus Python and `.inp` text. `run_cae_script` executes its CAE-authoring script, the host Solver tool (`solver_job` for ordinary sessions) executes the generated input deck, and `run_abaqus_python` executes its ODB extraction script; the typed tools separate runtime stages and evidence, not prohibit scripts.
- **Requested visual artifacts — Official API + 2024 reference.** When the requested deliverable includes a CAE viewport image or browser-viewable geometry, export them as ordinary workspace artifacts from the CAE script. `session.printToFile(..., format=PNG, ...)` produces the viewport image and `session.writeOBJFile(...)` produces OBJ geometry for supported displayed objects. Before PNG export, prefer an explicit web-preview size such as `session.pngOptions.setValues(imageSize=(1600, 1080))` instead of the default screen-sized output; keep the chosen dimensions within the local graphics limit. Generate only the visual artifact(s) needed by the request: PNG and OBJ are independent deliverables and neither requires the other.

## Route by requested outcome and available artifact

| Starting point | Normal approach |
|---|---|
| Existing `.py` | Inspect and edit the script. Execute it only when runtime validation or another requested deliverable requires execution. |
| Existing `.inp` | Edit the deck or run it when the requested outcome includes a solve. |
| Existing `.cae` | Write a CAE-kernel script that opens and modifies the model database. |
| Existing `.odb` | Use `abaqus python` for numerical extraction; use Viewer only for Visualization output. |
| No model artifact | Create a new Abaqus/CAE Python script in the chosen workspace. |

Do not force every request through a complete create-solve-postprocess workflow. A request may end after a parameter change, script repair, mesh edit, analysis-step change, input-deck generation, solve, extraction, or report.

## Working loop

1. Identify the requested deliverable and inspect the workspace before deciding what must be created or changed.
2. Ask only for information that blocks that deliverable. Do not demand a fixed geometry/material/load checklist for a local edit or post-processing task.
3. Select the Abaqus execution mode from the artifact and APIs involved.
4. When an API, keyword, output variable, or command option is uncertain, prefer exact Abaqus 2024 documentation or a small isolated runtime probe over guessing. Do not present 2026 documentation as exact 2024 evidence without noting the version mismatch.
5. Edit ordinary dynamic Abaqus Python or `.inp` text directly.
6. When execution is required by the requested deliverable, run with the configured Abaqus 2024 launcher in the case working directory. For a text-only change, inspect the target diff and do not execute a script whose unrelated code may submit a job or create other side effects.
7. Inspect evidence closest to the failed stage: Python traceback for authoring or extraction, `.log/.msg/.sta/.dat` for a solve, and the requested `.cae/.inp/.odb/JSON` artifact for stage completion. Do not infer success from one signal alone.
8. Fix the smallest issue supported by the evidence and rerun only the necessary stage.
9. Stop when the requested deliverable is produced and checked, or when missing engineering intent or an external runtime condition prevents further progress.

## Optional implementation references

When `knowledge_read_file` is available and an implementation example would help,
use its `README.md` or a known reference file directly. The local official beam
example's `beam-example.md` explains its source, loading, instance meshing and
adaptation limits; `beamExample.py` retains the original code. Reference reading
is optional and does not authorize the tutorial's embedded job submission.
The packaged [reading note](references/official-beam-example.md) is the source of
that short explanation; the vendor script is acquired separately from local docs.
Keep acquired source examples intact, including GUI/session operations. Adapt the
current workspace script to the requested runtime: omit display-only code when it
does not serve the deliverable, and never carry an example's `submit()` or
`waitForCompletion()` into a CAE authoring script. Solver execution belongs to
the host Solver tool so its budget and evidence remain visible to the host.

## Completion report

Report the target Abaqus release, the files changed, the command or execution mode used when applicable, the important produced artifacts, and whether the user's requested outcome was reached. When a narrow edit did not require execution, say so explicitly. State unresolved Abaqus warnings or assumptions without expanding a narrow task into an unsolicited full verification report.

## Design provenance

The embedded-Python boundary, mode routing, documentation lookup, and diagnostic-loop concepts were adapted from the MIT-licensed `svd-ai-lab/sim-plugin-abaqus` skill snapshot under `D:\Projects\abaqus-agent\references\sim-plugin-abaqus`. Commands and release facts here were revalidated against the local Abaqus 2024 installation. This skill has no runtime dependency on the reference checkout.
