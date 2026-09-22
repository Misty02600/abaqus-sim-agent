# Hyperelastic element formulation and procedures

Source page: `SIMACAEMATRefMap/simamat-c-hyperelastic.htm`, sections on elements, pure-displacement versus hybrid formulation, incompatible-mode elements, and procedures; Abaqus 2026 FD02.

Use this reference once the constitutive data are known and the unresolved issue is how to represent them in the FE model.

## Near-incompressibility and formulation

For three-dimensional solid, plane-strain, and axisymmetric hyperelastic models, strong near-incompressibility can make an ordinary displacement formulation unsuitable. Abaqus/Standard documentation recommends hybrid continuum formulations as incompressibility becomes strong.

Lower-dimensional formulations such as plane stress, membranes, shells, beams, trusses, and rebar have different volumetric constraints; do not transfer a three-dimensional continuum rule mechanically to them.

Abaqus/Explicit generally needs a usable compressibility for continuum hyperelastic response rather than the same fully incompressible treatment used by Standard hybrid elements.

## Structural checks

Hyperelasticity normally accompanies finite deformation, so preserve geometric nonlinearity. Inspect volumetric/pressure response where relevant, element distortion, mesh sensitivity, and locking or penalty-related behavior. A converged increment does not establish that the material fit or element formulation is sound.

Exact element labels and release-specific procedure/API details remain Abaqus 2024 evidence.
