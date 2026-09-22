# Beam element selection

Source page: `SIMACAEELMRefMap/simaelm-c-beamelem.htm` (*Choosing a Beam Element*), Abaqus 2026 FD02.

Use this reference when the structure may be represented by a beam centerline and the unresolved issue is the beam formulation or section assumptions.

## Representation first

A beam model needs a defensible reference line, section geometry/properties, section orientation, and loading/support description that can be represented through beam degrees of freedom. Use a three-dimensional continuum or shell representation when the requested local stress state cannot be recovered from a beam idealization.

## Main formulation choices

- Slender Euler-Bernoulli-type behavior is appropriate when transverse shear deformation is negligible for the quantity of interest.
- Shear-flexible Timoshenko-type behavior is relevant when transverse shear contributes materially to the response.
- Interpolation order, mass formulation, transverse shear stiffness, and rotary inertia matter for different static and dynamic uses.
- Open-section/warping beams require special attention when torsion and section warping are important.
- Hybrid beam formulations are a formulation choice for cases where the ordinary displacement formulation is unsuitable; do not infer the need for one from the word `hybrid` alone.

## Modeling data and checks

Keep section dimensions/properties, material assignment, local orientation, offsets, and connection assumptions explicit. Verify deformation mode, section force/moment resultants, reactions, and—when local stress matters—compare against a higher-dimensional model or analytical section solution.

Exact element labels and release-specific capabilities must be checked through Abaqus 2024 evidence when coding.
