# Natural frequency extraction

Source page: `SIMACAEANLRefMap/simaanl-c-freqextraction.htm` (*Natural Frequency Extraction*), Abaqus 2026 FD02.

Use this reference for eigenfrequency, mode-shape, participation, effective-mass, or modal-basis preparation tasks.

## Eigenproblem and solver choice

Natural frequencies and mode shapes depend on the model's mass, stiffness, constraints, and supported base state. Abaqus provides multiple eigensolvers, including Lanczos, AMS, and subspace iteration; method choice is a numerical/procedure decision, not a change in the physical structure.

Inspect mode shapes together with frequency values. Rigid-body, local, duplicated, or otherwise unintended modes often expose modeling problems that a frequency table alone hides.

## Prestressed base state

Frequency extraction is a linear perturbation about a base state. If it follows one or more general analysis steps, the base state is the current state at the end of the last general step. A prestressed-frequency workflow therefore has two distinct jobs: first establish the loaded configuration and stress state, then extract small-vibration modes about that state.

Initial-stress and load-stiffness effects enter the eigenproblem only when geometric nonlinearity was active in a preceding general step. Merely having an initial-stress definition or previously applied load is not enough. If an initial stress must influence the frequencies and no suitable nonlinear general step exists, insert a static general step with geometric nonlinearity enabled and maintain the intended stress state with the required loads and boundary conditions before frequency extraction.

Loads applied in the frequency-extraction step itself are ignored. Loads from the preceding general step can affect the eigenproblem through their load stiffness when that preceding step included geometric nonlinearity.

Plasticity, other inelastic behavior, and rate-dependent material behavior are inactive during the frequency-extraction perturbation. Prestressed modes therefore linearize about the established material/configuration state; they do not continue plastic flow, viscoelastic evolution, or another nonlinear constitutive history during the eigenproblem.

For prestressed-mode tasks, verify the provenance of the base state, whether the preceding step used geometric nonlinearity, and whether the loads/constraints that maintain the intended stress state were actually present at the end of that step.

## Normalization and modal completeness

Normalization changes the scale of a mode shape, not the physical eigenfrequency. Participation factors and modal effective mass help judge whether the extracted basis can represent a given direction/excitation.

Residual modes can extend a retained basis for modal-superposition use when supported by the procedure. Select enough modes or a sufficient frequency range to cover the excitation and requested quantity of interest; do not rely on a fixed mode count without a convergence check.

## Checks

Verify mass and units, boundary conditions, expected rigid-body modes, frequency convergence with mesh/model refinement, and stability of the response as the retained modal range grows.
