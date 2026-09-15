---
name: linear-structural-fea-postprocess
description: "Define or disambiguate linear structural FEA result semantics: extraction contracts, nodal versus element-nodal/integration-point values, extrapolation/averaging, section points, orientations, derived-value ordering, and peak/singularity meaning. Use when result meaning or extraction semantics are themselves unresolved or requested; do not load it merely because formulation or discretization mentions a QoI. Use release-specific Abaqus evidence for exact ODB APIs."
---

# Linear Structural FEA Postprocess

Use this skill when the meaning of the reported result is part of the engineering decision: selecting a stress value, comparing meshes, explaining a contour peak, extracting beam/shell through-section results, or defining a machine-readable quantity of interest (QoI). It does not teach the full ODB API.

Exact Abaqus 2024 API names, signatures, enums, defaults, and release behavior remain the responsibility of the project's release-specific evidence channels.

A tutorial that opens an ODB and displays a contour may provide no numerical extraction. Use the requested result definition below to decide what extraction code is needed; opening the example's result does not fulfill the current task.

## Define the extraction contract before reading a number

Terms such as `max stress` or `node stress` are incomplete. Preserve enough semantics that two values can be compared without silently changing the quantity:

- region or structural feature;
- analysis step and frame/state;
- result variable;
- result position, such as nodal or integration-point based;
- section point when the element has through-section output locations;
- coordinate/material orientation for direction-dependent components;
- component or invariant, including operation order when averaging and derivation can differ;
- aggregation such as one value, maximum, minimum, mean, resultant, or another explicit reduction.

Keep these semantics fixed during mesh convergence and before/after comparisons. Integration-point stress and a smoothed nodal contour are not the same QoI merely because both are labelled `S`.

## Distinguish node-based and element-based values at nodes

For Abaqus field output, keep these meanings separate:

- `NODAL` denotes values calculated at nodes, such as displacement;
- `ELEMENT_NODAL` denotes element-based values obtained by extrapolating integration-point results to each element's nodes;
- centroidal element values can likewise be derived from integration-point fields when the applicable output path performs that extrapolation.

Do not call an extrapolated `ELEMENT_NODAL` stress a solver-computed nodal stress. A shared geometric node can carry separate contributions from adjacent elements before any cross-element averaging.

## Keep extrapolation and averaging paths separate

Three common mechanisms have different semantics:

1. **Element result extrapolation to nodes**: each element extrapolates its integration-point field to its own nodes; adjacent-element contributions are still separate.
2. **Abaqus/Standard averaged-at-nodes output**: Standard can average extrapolated contributing element values while writing field output. For derived stress quantities, components are averaged first and the derived quantity is then formed from those averaged components.
3. **Viewer result averaging**: Visualization can conditionally average element-based contributions for display/reporting according to result regions and averaging settings. This is a post-read display/report operation and is distinct from solver/output-generation averaging.

Viewer smoothing or threshold settings do not redefine the underlying field uniquely. When displayed nodal stress is used as evidence, record the result/averaging options that produced it.

## Prefer integration-point values when extrapolation changes the question

Element stress/strain fields are fundamentally evaluated at integration/section points. Extrapolated values can be less reliable in strong gradients.

For stress concentration, local peaks, material-point response, and mesh-convergence evidence, start from integration-point data unless the engineering question explicitly needs a nodal representation. If a nodal representation is needed, state how it was produced.

Derived quantities can depend on operation order. Averaging tensor components and then forming Mises/principal values can differ from forming an invariant first and averaging those scalar values. Preserve the intended order as part of the QoI.

## Preserve section-point semantics

Beam, pipe, shell, and layered-solid results can vary through the section. When the result depends on section location, include the section point in the QoI instead of collapsing top/bottom, beam section, or layer values into an unnamed `stress` result.

Exact output-request syntax and ODB section-point APIs are version-specific implementation details. Confirm them through the Abaqus 2024 evidence channels when needed.

## Preserve coordinate and material-direction semantics

Stress, strain, and vector components depend on direction. Do not compare `S11`, `S22`, shear components, or vector components across regions/models without confirming that their coordinate or material directions are equivalent.

When orientation varies spatially, extrapolated nodal values deserve extra care because element/integration-point directions can differ. Direction-sensitive QoIs should name the relevant coordinate/material orientation explicitly.

## Treat peak values and singularities as result-definition questions

A growing maximum stress under refinement does not by itself show improvement or failure. Sharp re-entrant corners, point loads, idealized fixed boundaries, and similar singular idealizations can make a pointwise peak unsuitable as a convergence target.

When a peak does not converge, ask whether that pointwise maximum is physically meaningful for the intended engineering claim. If justified by the task, use a regularized quantity such as a value at a defined location, an averaged/resultant measure, or a quantity tied to a physical failure model. Do not invent a smoothing radius or acceptance threshold.

## Route related questions to their owners

Use discretization for the refinement procedure and discretization error. Use verification for equilibrium, independent-reference checks, and the claim boundary of solution verification. Use exact Abaqus evidence when the unresolved issue is an ODB/API binding rather than result meaning.

## Evidence and maintenance boundary

Read [references/evidence.md](references/evidence.md) when auditing, extending, or disputing these rules. Ordinary result tasks should use the concise operational guidance above.
