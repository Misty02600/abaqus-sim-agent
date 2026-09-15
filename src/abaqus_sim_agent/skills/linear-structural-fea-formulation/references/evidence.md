# Evidence for `linear-structural-fea-formulation`

This reference records the sources supporting the current formulation boundaries. It is intentionally narrower than a structural-mechanics textbook and does not provide missing task parameters or Abaqus 2024 API compatibility.

## Problem definition and idealization

- A. F. Bower, *Applied Mechanics of Solids*, [Chapter 1.1](https://solidmechanics.org/text/Chapter1_1/Chapter1_1.htm): used for the separation between a physical problem, the quantities of interest, and the idealized mathematical model.
- Bower, [Chapter 7.1: Guide to using finite element software](https://solidmechanics.org/text/Chapter7_1/Chapter7_1.htm): used as a general FEA workflow source for structural idealization, elements, material behavior, boundary conditions, loads, units, and output interpretation.

These sources support deciding the representation from the problem being answered rather than from a single geometry ratio. They do not establish Abaqus-specific element/API bindings.

## Linear-analysis assumptions

- K. J. Bathe, MIT OpenCourseWare, [Finite Element Procedures for Solids and Structures — Linear Analysis](https://ocw.mit.edu/courses/res-2-002-finite-element-procedures-for-solids-and-structures-spring-2010/video_galleries/linear/): used for the linear-analysis framework based on linear elastic response and infinitesimal displacement/strain assumptions, and for the separation between linear and nonlinear formulations.

This evidence supports rejecting a single global `<1%`/`<5%` displacement threshold as the definition of linear applicability. The current skill therefore states assumptions and exit conditions rather than a universal percentage.

## Static versus dynamic response

- SIMULIA, [Abaqus Fundamentals for 3DEXPERIENCE SIMULIA: Introduction to Finite Element Analysis](https://3dswym.3dexperience.3ds.com/post/simulia-community/abaqus-fundamentals-for-3dexperience-simulia-introduction-to-finite-element-analysis_aNI2FBVRRr6LbQd5JzKqUA): used for the distinction between static equilibrium response and analyses where time/frequency variation and inertia require a dynamic treatment.

The source supports separating the questions "is the material/kinematics linear?" and "can inertia/time dependence be neglected for the QoI?".

## Abaqus-specific dimensionality

- Abaqus documentation mirror, [Choosing the Element's Dimensionality](https://abaqus.uclouvain.be/English/SIMACAEELMRefMap/simaelm-c-dimension.htm): used only as candidate Abaqus-specific conceptual support for dimensional idealization.

The mirror's release has not been established as Abaqus 2024. Do not use it as exact 2024 compatibility evidence. The project's 2024 Exact API Catalog, licensed runtime evidence, or matching release documentation must confirm any version-sensitive implementation detail.

## Related project audit

- `docs/scratch/linear-structural-fea-source-topic-matrix.md`
- `docs/scratch/linear-structural-fea-evidence-bindings.md`

These audit files preserve the source comparison, rejected community heuristics, and the decision to keep formulation knowledge distinct from exact Abaqus APIs and host verification.
