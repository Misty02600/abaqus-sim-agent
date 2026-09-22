# Hyperelasticity: modeling principles

Use this reference when the unresolved question is the mechanics of finite-strain elasticity, incompressibility, calibration coverage, or constitutive validity rather than an Abaqus-specific option.

## Finite-strain elastic response

Hyperelasticity represents recoverable nonlinear elastic response through a strain-energy function. The constitutive model must remain objective under rigid-body rotation and must be interpreted with finite-strain kinematics; a small-strain elastic law extrapolated to large deformation is not an equivalent model.

Geometric nonlinearity and hyperelasticity are related but separate. Finite deformation changes the current geometry and equilibrium, while the hyperelastic law defines how the material stores energy as deformation changes. A model can require both decisions independently.

## Deviatoric and volumetric response

Rubber-like solids are often nearly incompressible: distortional deformation can be large while volume change remains small. This creates a strong separation between shear/deviatoric stiffness and bulk/volumetric stiffness.

Near-incompressibility is therefore both a material assumption and a discretization issue. An unrealistically large bulk stiffness can create locking or conditioning problems, while an unrealistically soft volumetric response changes the physical deformation. Do not infer the volumetric law from a material name alone.

## Calibration coverage and identifiability

A material law fitted to one deformation mode is not uniquely validated for arbitrary multiaxial loading. Different strain-energy functions can fit the same uniaxial curve yet predict different biaxial, planar, shear, or confined responses.

Judge a fit against the deformation modes and strain range that the structure will actually experience. Treat extrapolation beyond the calibrated range as an explicit uncertainty. If volumetric data are absent, the compressibility assumption remains separate evidence rather than a fitted fact.

## Stability and structural use

Numerically excellent curve fitting does not by itself establish a physically usable constitutive law. Check whether the selected model remains stable and produces plausible stresses and energy over the intended deformation range and loading modes.

For structural verification, compare simple homogeneous deformation states where possible, inspect reaction/load response and deformation mode, and check sensitivity to mesh and volumetric formulation. If the structural solution reaches strain states outside the calibration envelope, report that limitation rather than interpreting the extrapolation as validated material behavior.

This file contains general finite-strain modeling principles. Abaqus strain-energy families, calibration facilities, hybrid-formulation guidance, and solver-specific behavior remain in the package's Abaqus official topic references.
