# Evidence for `linear-structural-fea-modeling`

This reference records the authority and limits behind the modeling rules. It is maintenance evidence, not a source of missing task parameters and not Abaqus 2024 API compatibility evidence.

## General modeling workflow

- A. F. Bower, *Applied Mechanics of Solids*, [Chapter 1.1](https://solidmechanics.org/text/Chapter1_1/Chapter1_1.htm) and [Chapter 7.1](https://solidmechanics.org/text/Chapter7_1/Chapter7_1.htm): used for the progression from a physical mechanics problem to material behavior, supports, loads, finite-element idealization, and interpreted output.
- NAFEMS, public **Basic Finite Element Analysis** course/module material: used as a high-level workflow and model-checking coverage source, not as authority for unpublished numerical thresholds.

These sources support keeping physical problem definition separate from software binding and keeping unit/material/load/support choices tied to the actual engineering problem.

## Abaqus conceptual semantics

The following local Abaqus 2026 FD02 pages were checked for conceptual semantics:

- `SIMACAECAERefMap/simacae-c-prppropmaterial.htm`: material definitions and their relation to section assignment;
- `SIMACAECAERefMap/simacae-c-prppropsection.htm`: section/property meaning, including reduced-dimensional property information;
- `SIMACAECAERefMap/simacae-t-lbibceditorsdisplacement.htm`: displacement/rotation boundary-condition semantics;
- `SIMACAECAERefMap/simacae-t-lbiloadeditorsconcforce.htm`: concentrated-force semantics;
- `SIMACAECAERefMap/simacae-t-lbiloadeditorsbodyforce.htm`: body-force semantics;
- `SIMACAECAERefMap/simacae-t-lbiloadeditorspressure.htm`: pressure/distributed-load semantics, including the distinction between intensity and total force.

The project's full local documentation corpus is Abaqus 2026 FD02. These pages therefore support conceptual modeling semantics only. Exact Abaqus 2024 object names, signatures, defaults, ownership, and release compatibility must come from the 2024 Exact API Catalog, matching release documentation, or licensed runtime evidence.

## Rejected generalizations

The maintained source audit records community examples that hard-code tutorial material values, automatically add density, over-fix models after zero-pivot symptoms, or conflate pressure intensity with total force. These are recurring failure modes, not rules to adopt.

No community default may fill a missing user-specific material constant, thickness/profile, support condition, load magnitude/direction/distribution, prescribed motion, or unit interpretation.

## Related project audit

- `docs/scratch/linear-structural-fea-source-topic-matrix.md`
- `docs/scratch/linear-structural-fea-evidence-bindings.md`
- `docs/scratch/linear-structural-fea-routing.md`
