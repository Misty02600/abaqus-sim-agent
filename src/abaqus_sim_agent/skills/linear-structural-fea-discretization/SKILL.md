---
name: linear-structural-fea-discretization
description: Choose or review finite-element discretization for linear structural FEA, including representation, interpolation order, integration, locking/hourglass risks, mesh refinement, and QoI-based convergence. Use when discretization choices materially affect a linear structural model; use release-specific Abaqus evidence for exact element and API bindings.
---

# Linear Structural FEA Discretization

Use this skill to decide whether a discretization is suitable for the requested response and to identify the numerical risks that must be checked. Keep the reasoning at the formulation and mesh level. Exact Abaqus element names, keyword syntax, object ownership, and API signatures belong to release-specific Abaqus documentation, the Exact API Catalog, or licensed runtime evidence.

This skill assumes the task is still within a linear structural FEA scope. If the response depends materially on large deformation, material nonlinearity, contact-state changes, instability/buckling, impact, vibration, or other dynamics, stop applying linear-discretization conclusions as if they were sufficient and route the unresolved modeling question to the relevant knowledge source.

## Start from the requested response

Do not choose an element code from geometry alone. Establish only the facts needed for the current discretization decision:

- the quantity of interest (QoI), including where and in what direction or component it is evaluated;
- the structural representation already intended or justified, such as beam, shell, plane, axisymmetric, or continuum solid;
- whether bending, local stress gradients, near-incompressibility, curvature, or severe mesh distortion is expected to matter;
- the load and constraint features that create the response being resolved.

Read these facts from the task and workspace when available. If a missing user-specific physical fact would change the structural idealization or the meaning of the QoI, ask for that fact rather than supplying a template value. Do not require unrelated inputs for a narrow mesh or element-review task.

## Choose the discretization by mechanism

Reason in this order: **representation -> approximation/integration -> known risk -> required check -> Abaqus binding**.

### 1. Check that the representation can express the QoI

Mesh refinement cannot repair a structural idealization that omits the response of interest. Before refining a mesh, check whether the chosen dimensional/structural representation retains the deformation and stress result the task needs.

Do not use a universal aspect-ratio rule such as `t/L < 0.1 -> shell` or `L/d > 10 -> beam` as a substitute for that judgment. Geometric slenderness is relevant evidence, but the retained kinematics, loading, constraints, and QoI determine whether the idealization is acceptable.

### 2. Choose interpolation order with the response in mind

Second-order continuum elements are often advantageous for smooth problems, curved boundaries, bending, and local stress representation when the mesh is not severely distorted. They are not universally superior: cost, distortion sensitivity, element formulation, and the actual QoI still matter.

First-order elements can be entirely appropriate when their formulation represents the target response adequately. Treat element order as one choice within the formulation, not as a global accuracy ranking.

### 3. Treat integration choice and locking/hourglass risk together

For continuum elements, do not discuss full versus reduced integration without also naming the failure mode being traded:

- first-order full-integration elements can become artificially stiff in bending because of shear locking;
- near-incompressibility can produce volumetric locking in susceptible formulations;
- first-order reduced-integration elements can admit hourglass or other zero-energy modes;
- hourglass control reduces a known numerical mode but does not prove that the mesh or response is accurate.

When near-incompressibility matters, select a formulation intended to handle that regime and confirm the exact Abaqus-supported option from release-specific documentation. Do not infer a solver-specific element code from this skill alone.

### 4. Refine where the QoI needs resolution

Use mesh density to resolve gradients and the requested response. Common reasons for local refinement include geometry changes, holes or fillets, load-introduction regions, constraint transitions, contact with the scope boundary of another analysis, and other locations where the QoI varies rapidly.

Mesh-quality measures such as distortion or aspect metrics are diagnostics. Passing them does not establish result accuracy, and failing a generic community threshold does not by itself prove the solution is unusable.

Do not promote case-specific rules such as "always use 3-4 elements through thickness" into a general requirement. Through-thickness resolution must follow the chosen element formulation, bending behavior, and QoI.

### 5. Check the failure mode implied by the chosen formulation

After choosing a candidate discretization, inspect evidence that would reveal its characteristic failure mode. Depending on the choice, this can include:

- an implausibly stiff bending response or strong sensitivity to element formulation;
- nonphysical hourglass deformation or zero-energy patterns;
- excessive sensitivity to mesh distortion;
- unstable or nonconvergent peak-stress behavior near geometric/load/constraint singularities;
- a QoI that changes materially under targeted mesh refinement.

Use solver energy quantities only when their meaning is understood for the current analysis and formulation. Do not apply a fixed `ALLAE/ALLIE` percentage as a universal pass/fail criterion.

## Use QoI-based mesh convergence

When the task requires confidence in discretization error, compare the same precisely defined QoI across a purposeful refinement sequence. Keep the region, step/frame, result position, component or invariant, and aggregation consistent enough that the values are comparable.

Refine the part of the mesh that controls that QoI instead of using total element count as the convergence measure. A converged displacement does not prove a local stress peak is converged, and a mesh-quality score does not substitute for solution verification.

If the requested QoI is a mathematical or modeling singularity, such as an unconstrained peak stress at an idealized sharp feature or point load, ordinary maximum-stress convergence may be the wrong acceptance target. Escalate the result-definition question rather than chasing an ever larger maximum.

Mesh convergence is **solution verification** for the current computational model. It does not validate that the user's geometry, material, loads, or boundary conditions represent physical reality.

## Bind the decision to Abaqus only after the mechanism is clear

Once the needed formulation is understood, use the Abaqus 2024 knowledge channels to confirm the exact implementation:

- use release-specific element documentation for available formulations and product limitations;
- use the Exact API Catalog for exact names, parameters, enum/constants, and object ownership;
- use runtime evidence when a dynamic Abaqus object or licensed behavior remains uncertain.

Do not duplicate element-code tables or scripting recipes here. A familiar Abaqus element is a candidate implementation of an engineering decision, not the decision itself.

An example's element code and mesh size are choices for that example, not universal defaults such as "C3D8R first". When using an implementation reference, preserve the distinction between Part meshing and independent-instance meshing; use exact Abaqus evidence to confirm the appropriate calls.

## Evidence and maintenance boundary

Read [references/evidence.md](references/evidence.md) when auditing, extending, or disputing a rule in this skill. It records the authority and limits of the current evidence. For ordinary use, the decision rules above should be sufficient; do not load the evidence reference merely to repeat the same guidance.

When an Abaqus-specific element-family choice is unresolved, use the shared linear-foundation references in `../linear-structural-fea-modeling/references/`: `beam-element-selection.md`, `shell-element-selection.md`, or `solid-element-selection.md`. These summarize the 2026 FD02 product semantics; exact Abaqus 2024 labels and options remain release-specific evidence.
