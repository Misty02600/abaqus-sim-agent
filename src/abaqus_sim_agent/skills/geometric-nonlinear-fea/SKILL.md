---
name: geometric-nonlinear-fea
description: "Model or review geometric-nonlinear structural response caused by large displacement, large rotation, changing equilibrium geometry, follower effects, or prestress. Use for kinematic nonlinearity; route constitutive and contact mechanisms to their own skills."
---

# Geometric Nonlinear FEA

This is the project’s scoped meaning of the former broad “nonlinear” package. Use it for **kinematic/geometric nonlinearity**: large displacement or rotation, materially changing geometry, follower effects, stress stiffening, or equilibrium paths that cannot be represented by a small-deformation formulation.

Material nonlinearity belongs to the relevant constitutive skill, and contact belongs to `contact-fea`. A model may require several of these skills at once.

## Decide from the mechanics

- Ask whether the deformed configuration materially changes stiffness, equilibrium direction, contact state, or the requested response.
- Large rotation can require geometric nonlinearity even when strains stay small.
- Prestress can affect later modal behavior; preserve the intended step sequence when a frequency extraction depends on a preceding nonlinear static state.
- Instability, snap-through, postbuckling, or severe softening may require a procedure suited to tracing the relevant equilibrium path; a generic static step is not automatically adequate.

## Numerical checks

Inspect increment history, load/displacement progression, deformation shape, reaction balance, and sensitivity to imperfection or stabilization when those features are part of the model. Convergence difficulty can be physical or numerical; do not respond by changing controls before identifying which mechanism is active.

Read [references/modeling-principles.md](references/modeling-principles.md) when the unresolved issue is large rotation versus large strain, configuration-dependent equilibrium, prestress/geometric stiffness, limit points, bifurcation, or imperfection sensitivity. Read [references/finite-strain-output-measures.md](references/finite-strain-output-measures.md) when interpreting `S`, `E`, `NE`, or `LE`, choosing a finite-strain measure, or comparing Abaqus output with experimental/DIC strain. Keep Abaqus-specific `nlgeom` and procedure semantics in the official references.

## Official implementation reference

Read [references/abaqus-official-notes.md](references/abaqus-official-notes.md) when the effect of `nlgeom`, evolving directions/stiffness, step history, or Abaqus finite-strain output conventions is uncertain. Read [references/instability-buckling-and-path-following.md](references/instability-buckling-and-path-following.md) for eigenvalue buckling versus nonlinear collapse/postbuckling, mode-based imperfections, Riks path following, proportional loading, or LPF interpretation. Read [references/official-examples.md](references/official-examples.md) for case navigation. The nonlinear skew-plate script is the canonical isolated example because it uses a shell model with `StaticStep(..., nlgeom=ON)`. The rubber mount and lap joint are cross-references where geometric nonlinearity interacts with hyperelasticity or contact/plasticity.
