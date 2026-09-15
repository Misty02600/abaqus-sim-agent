# Evidence for `linear-structural-fea-discretization`

This file records why the current decision rules are present and where their authority stops. It is maintenance evidence, not an Abaqus 2024 API compatibility source and not a collection of default engineering thresholds.

## Element order, integration, locking, and hourglass behavior

Primary source:

- SIMULIA, **Abaqus Fundamentals for 3DEXPERIENCE SIMULIA: Solid Continuum Elements**: <https://3dswym.3dexperience.3ds.com/post/3dexperience-edu-students/abaqus-fundamentals-for-3dexperience-simulia-solid-continuum-elements_h6q-hykuQ_6kJDU6W1igpw>

The source supports the following bounded conclusions used by the skill:

- Abaqus solid elements combine choices such as first/second order, full/reduced integration, hybrid, and incompatible-mode formulations; element selection is therefore multi-dimensional.
- Second-order elements are often more accurate for smooth, not-severely-distorted problems and can represent curved boundaries, bending, and local stress fields effectively; this does not establish a universal ranking.
- First-order full-integration elements can exhibit shear locking in bending.
- Near-incompressible behavior can lead to volumetric locking in susceptible formulations.
- First-order reduced-integration elements can exhibit hourglassing; available hourglass control does not remove the need to inspect the mesh and response.

The source is used for formulation concepts. Exact Abaqus 2024 element/API compatibility must still come from the project's 2024 Catalog, release-specific documentation, or licensed runtime evidence.

## QoI and solution-verification boundary

Primary sources:

- NAFEMS, **Code Verification v Solution Verification**: <https://www.nafems.org/blog/posts/code-verification-v-solution-verification/>
- NAFEMS, **R0135 Code Verification Exemplars in Computational Solid Mechanics** public resource page: <https://www.nafems.org/publications/resource_center/r0135/>
- NAFEMS glossary, verification/validation terminology: <https://www.nafems.org/publications/glossary/terms_s-z/>

These sources support keeping mesh refinement tied to a defined QoI, treating mesh convergence as solution verification for the current computational model, and separating that claim from validation against physical reality.

The project has not acquired the full R0135 publication. Its public page may support terminology and the existence/organization of exemplar topics, but this skill must not infer benchmark details, numerical tolerances, or reference values from the public table of contents.

## Related project audit

The maintained audit that led to this skill is in:

- `docs/scratch/linear-structural-fea-source-topic-matrix.md`
- `docs/scratch/linear-structural-fea-evidence-bindings.md`

Those files also record community candidates and rejected heuristics, including fixed `<1%`/`<5%` linearity thresholds, fixed through-thickness element counts, universal `C3D8R first` guidance, and fixed hourglass-energy limits. Their presence in community material is evidence that these questions recur, not authority for adopting the numbers.
