---
name: structural-dynamics-fea
description: "Choose and review direct structural dynamic analyses involving inertia, transient response, damping, excitation, and time resolution. Use modal-dynamics-fea for eigenfrequency or modal-basis tasks, and explicit-dynamics-fea for explicit time integration."
---

# Structural Dynamics FEA

Use this skill when inertia, natural modes, transient response, or damping matters. Density and other mass properties become physical inputs here even when they were unnecessary in a static analysis.

## Choose the dynamic question first

- Frequency extraction and modal-basis response belong to `modal-dynamics-fea`.
- Direct time integration resolves the equations in time and requires a time increment/frequency-content choice consistent with the excitation and requested QoI.
- A quasi-static problem should not be turned into a dynamic one merely to obtain numerical convergence.

## Preserve excitation and damping semantics

Separate force, base motion, initial conditions, and prescribed motion. Record whether damping is modal, material, Rayleigh, or another form and whether its calibration is supported by data. Time-history duration and sampling must resolve both the excitation and the structural response of interest.

## Verification focus

Check mass and units, time-step sensitivity, energy behavior, damping assumptions, excitation frequency content, and convergence of the QoI with temporal resolution.

Read [references/modeling-principles.md](references/modeling-principles.md) when the unresolved issue is dynamic equilibrium, structural/excitation time scales, temporal accuracy, or damping meaning rather than an Abaqus procedure.

## Official implementation reference

Use [references/abaqus-official-notes.md](references/abaqus-official-notes.md) as the official-reference index. For direct transient work, load `procedure-selection-and-damping.md` and/or `direct-implicit-dynamics.md`; modal work routes to `modal-dynamics-fea`. Read [references/official-examples.md](references/official-examples.md) for the shared package examples.
