---
name: linear-structural-fea-formulation
description: "Frame a basic linear structural FEA problem by defining the physical QoI, judging linear/static applicability, choosing the structural idealization, and identifying missing user-specific inputs that would change the model. Use when those assumptions are unresolved; skip it when an existing model's formulation is already accepted and the task only edits known material/property/load/BC values."
---

# Linear Structural FEA Formulation

Use this skill to decide what mathematical/structural problem the finite-element model is meant to represent before choosing detailed elements or writing Abaqus code. Keep physical assumptions, numerical choices, and exact Abaqus implementation separate.

This skill covers basic linear structural analysis; use the applicability checks below for the current response, even when an example script runs successfully.

## Define the requested response first

Establish the quantity of interest (QoI) closely enough that later idealization, discretization, output, and verification decisions can be judged against it. A useful QoI identifies, as applicable:

- the physical response, such as displacement, strain, stress, reaction, stiffness, or a derived quantity;
- the region or structural feature where it matters;
- the direction, component, or invariant needed;
- whether the user needs a local value, distribution, resultant, average, maximum, or another aggregation;
- the load case or state at which it is evaluated.

Do not turn every task into a large requirements interview. For a narrow edit, use the existing model and task statement unless a missing fact would materially change the physical meaning of the requested change.

## Separate known physical facts from modeling assumptions

Use user-provided facts and trustworthy workspace artifacts first. Typical formulation inputs include geometry relevant to the response, material behavior in the target load range, loads, supports/constraints, and any time dependence relevant to inertia.

Treat an engineering assumption as an assumption, not as a recovered fact. Example values, community templates, benchmark data, and prior tasks do not supply a missing physical parameter for the current model.

If two plausible interpretations would materially change the governing behavior, idealization, load path, constraint meaning, or acceptance criterion, ask for the unresolved physical fact. Do not ask the user to choose an Abaqus API or element code when the remaining choice is implementation-level.

## Judge linearity from assumptions, not a universal percentage

The first-version linear scope requires all of the following to be reasonable for the response of interest:

- the constitutive response can be treated as linear elastic over the target load range;
- small-strain/small-displacement kinematics are adequate for the response being evaluated;
- deformation does not materially change load direction, contact state, support state, or another mechanism that makes the structural response nonlinear;
- instability or bifurcation is not the phenomenon being sought.

Do not use a universal rule such as displacement `<1%` or `<5%` of a characteristic dimension as the linear/nonlinear switch. Different structures and QoIs become sensitive to geometric nonlinearity under different conditions.

If a linear result is being used only as a deliberate approximation, preserve that modeling choice and its limitation rather than silently presenting it as generally valid.

## Judge static applicability separately from linearity

Linear material behavior does not imply static behavior. A static idealization is appropriate only when the requested response does not materially depend on inertia, transient propagation, vibration, frequency response, or another time-dependent effect that must be retained.

Impact, vibration, and explicitly time-dependent inertial response are outside this basic static scope. When the loading history exists only to reach an equilibrium state and inertial effects are negligible for the QoI, a static treatment may be reasonable; document that as an analysis assumption rather than a property of the material model.

## Choose a structural idealization that preserves the QoI

Select beam, shell, plane, axisymmetric, or continuum-solid representations from the combination of geometry, admissible kinematics, loads, constraints, and the QoI. Geometry ratios can inform this choice but do not replace it.

Do not encode universal shortcuts such as `t/L < 0.1 -> shell` or `L/d > 10 -> beam`. A reduced-dimensional model is useful only when the omitted dimensions and kinematics are not needed to answer the requested question.

For any 2D reduction, make the reduced assumption explicit. A geometrically flat part is not by itself enough to choose plane stress, plane strain, or axisymmetry; the corresponding loading, constraint, and through-thickness/circumferential assumptions must also be compatible. Confirm Abaqus-specific formulation details from the matching product documentation when needed.

## Hand off numerical decisions after the formulation is clear

Once the structural representation and linear/static scope are established, use the discretization skill for element order, integration, locking/hourglass risks, mesh refinement, and QoI-based convergence. Use release-specific Abaqus knowledge for exact element codes, output variables, keywords, and scripting APIs.

Do not use a successful solve as evidence that the formulation was physically appropriate. Runtime evidence can establish what the software executed; formulation adequacy depends on the assumptions and the intended response.

## Exit or clarify when the current scope is insufficient

If the linear/static checks above do not hold, use more specialized knowledge; nonlinear constitutive behavior includes plasticity, hyperelasticity, damage and viscoelasticity. Missing user-specific facts call for a focused clarification. A tutorial's successful run does not justify changing the intended model to fit that tutorial.

## Evidence and maintenance boundary

Read [references/evidence.md](references/evidence.md) when auditing or extending the formulation rules. Exact Abaqus 2024 compatibility remains outside this evidence file and belongs to the project's release-specific Catalog/document/runtime channels.
