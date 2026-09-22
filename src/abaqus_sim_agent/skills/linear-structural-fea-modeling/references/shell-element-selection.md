# Shell element selection

Source page: `SIMACAEELMRefMap/simaelm-c-shellelem.htm` (*Choosing a Shell Element*), Abaqus 2026 FD02.

Use this reference when a midsurface/thickness representation is plausible and the unresolved decision concerns conventional versus continuum shells, thin/thick behavior, strain formulation, or integration.

## Representation and section data

A shell model needs a defensible reference surface, thickness/section definition, shell normal and material/section orientation. If through-thickness normal stress or a genuinely three-dimensional local state is a primary quantity of interest, a shell idealization may be insufficient.

## Main formulation choices

- Conventional shells and continuum shells have different kinematics and modeling workflows; choose from the physical representation rather than convenience.
- Thin versus thick behavior determines whether transverse shear deformation needs to be represented explicitly.
- Finite-strain versus small-strain shell formulations matter when large membrane strain, thickness change, or large rotations interact with the requested response.
- Reduced integration and section controls can affect hourglass behavior and bending response; they are not interchangeable with mesh refinement.
- Five- and six-degree-of-freedom shell families have different rotational treatment and modeling implications.

## Checks

Inspect shell normals/orientations, thickness and offsets, membrane/bending resultants, section-point quantities when required, deformation shape, and mesh sensitivity. For curved or bending-dominated shells, compare against an analytical or benchmark solution where possible.

Exact shell element names and Abaqus 2024-specific options remain release-specific evidence.
