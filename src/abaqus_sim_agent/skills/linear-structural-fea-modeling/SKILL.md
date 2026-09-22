---
name: linear-structural-fea-modeling
description: "Translate known physical facts into, or make narrow meaning-level edits to, a basic linear-static structural model: units, material/section properties, supports, load semantics, force/displacement control, and symmetry. Use after formulation is fixed, including accepted-model changes to material, section, load, or support values; do not use it to invent missing physical inputs or resolve exact Abaqus bindings."
---

# Linear Structural FEA Modeling

Use this skill after the structural idealization and linear/static scope are clear. Its job is to preserve the physical meaning of known inputs while turning them into a complete boundary-value problem. Exact Abaqus 2024 object names, signatures, defaults, and ownership remain the responsibility of the release-specific Catalog/document/runtime evidence channels.

If a missing user-specific fact would materially change the model, route back to formulation and clarify it. Examples, retrieved documents, and community templates can identify what must be specified; they do not supply the missing value.

## Keep one consistent dimensional system

Treat unit consistency as a model invariant rather than choosing a preferred unit system. Geometry, elastic properties, loads, prescribed motions, mass-related inputs when used, and interpreted results must be dimensionally consistent.

Check the physical dimensions as well as labels. Elastic modulus is force per area; pressure or traction is force per area; concentrated force is force; body-force density is force per volume. Do not silently convert ambiguous inputs or guess the user's intended units.

## Treat material and property assignment as physical input

For a basic isotropic linear-elastic model, Young's modulus and Poisson's ratio are constitutive inputs. They must come from the task, an accepted source, or an explicit user-approved assumption. A material name or tutorial example is not evidence for a particular value.

Do not require density automatically for every mechanical linear-static problem. Density is needed when the load or analysis depends on mass or inertia, such as gravity/acceleration loading or dynamics.

Geometry alone does not complete structural stiffness. Make sure every deformable region receives the material/section/property information required by its representation. Reduced-dimensional models can require additional thickness, profile, or cross-section data. A missing or wrongly assigned property changes the model; it is not merely bookkeeping.

If anisotropy, plasticity, hyperelasticity, viscoelasticity, damage, or another constitutive mechanism materially controls the QoI, this basic modeling skill is insufficient.

## Model supports from admissible physical motion

Represent what the support or connection actually prevents while removing unintended rigid-body motion from the complete model.

- constrain only translations or rotations the physical support prevents;
- account for interactions, couplings, symmetry conditions, rigid bodies, or other connections that already restrict motion;
- check the complete model for remaining rigid-body modes.

Do not repair a singularity or zero-pivot symptom by mechanically adding fixed degrees of freedom. Underconstraint and overconstraint are different errors. Extra constraints can create artificial load paths, reactions, and local stresses even when the algebraic system becomes solvable.

A nonzero prescribed displacement is itself a physical boundary condition. Use it because the imposed motion is known or deliberately chosen, not merely because it makes the solve convenient.

## Preserve load meaning before choosing implementation objects

Identify what is physically known about each load: region, direction, spatial distribution, and whether the supplied magnitude is an intensity or a total resultant.

- A concentrated force is point/node-like and can introduce a local singular idealization when used for a finite contact area.
- A traction or pressure is distributed over a boundary. Pressure intensity and total force are not interchangeable unless area and distribution are defined.
- A body force is distributed through volume; interpret it with force-per-volume semantics or the applicable mass/acceleration relation.
- A prescribed displacement imposes motion and lets the required reaction emerge from structural stiffness.

Two models with the same scalar magnitude but different directions, loaded regions, or distributions are different boundary-value problems. If the task gives only a total load and the FE model requires a distribution, clarify or use an explicitly authorized assumption instead of inventing the distribution.

## Choose force or displacement control from the known physical quantity

In a linear static model, force and displacement are related by the linear stiffness operator, but arbitrary force-controlled and displacement-controlled problems are not automatically interchangeable.

Use force control when the applied resultant/distribution is the known input and displacement or compliance is a response. Use displacement control when imposed motion is the known input and the required reaction/resultant is a response. Treat them as equivalent only when they represent the same load path and kinematic pattern.

This also suggests useful checks: force-controlled cases naturally expose response displacement and reaction balance; displacement-controlled cases expose the reaction required to enforce the motion; proportional scaling should hold when the complete model is genuinely linear.

## Use symmetry only when the whole boundary-value problem permits it

Geometric symmetry alone is insufficient. A reduced model is valid only when geometry, material/property distribution, loads, supports/interactions, and the response represented by the QoI are compatible with the same symmetry.

Symmetry conditions suppress motion across the cut boundary and can remove a physically admissible antisymmetric response. Do not reduce the model merely to save elements when the loading, support, material, instability mode, or requested response breaks the symmetry.

When using a reduced symmetric model, keep global resultant scaling explicit and state whether reported loads/reactions belong to the reduced domain or the reconstructed full structure.

## Bind to Abaqus only after the model meaning is fixed

Once the physical meaning is fixed, use exact Abaqus evidence for uncertain bindings. An implementation reference can help connect geometry, section and load operations; when knowledge_read_file is available, its README.md describes the supplied examples. Compare their load region, direction and distribution with this task before adapting code.

When choosing a basic structural representation example, [references/official-examples.md](references/official-examples.md) records the shared official-pool anchors for truss/wire, solid, shell/plate, and beam models. They are navigation examples, not substitutes for the formulation, discretization, or release-specific API evidence required by the current task.

Read [references/abaqus-official-notes.md](references/abaqus-official-notes.md) as the official-reference index. Load only the matching topic file: static procedure, beam selection, shell selection, or solid selection. This keeps the full element-selection pages out of ordinary context.

Use the discretization skill when the unresolved question is element formulation, integration, mesh, or convergence. Use postprocess when the unresolved question is what a result value means. Use verification when judging the credibility or claim boundary of a completed result.

## Evidence and maintenance boundary

Read [references/evidence.md](references/evidence.md) when auditing, extending, or disputing these rules. Ordinary tasks should not load it merely to repeat the operational guidance above.
