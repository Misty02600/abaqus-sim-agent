---
name: hyperelastic-fea
description: "Model or review finite-strain hyperelastic structural problems once the material law/data are known: incompressibility, hybrid formulations, large deformation, discretization, and result checks. Use hyperelastic-calibration when identifying or fitting the constitutive parameters."
---

# Hyperelastic FEA

Use this skill when a rubber-like or other finite-strain elastic constitutive law materially controls the response. Treat hyperelasticity as a **constitutive** topic. Large deformation is related but separate: a hyperelastic problem normally needs geometric nonlinearity, while `nlgeom=ON` by itself does not make a material hyperelastic.

## Preserve the constitutive input

- Identify the strain-energy model and the provenance of its parameters before using them in the structural model.
- Do not infer coefficients from a generic material label such as rubber, silicone, PDMS, or Ecoflex. Use `hyperelastic-calibration` when the unresolved work is fitting or validating those parameters from test data.
- Check whether the material is incompressible or nearly incompressible and whether the selected formulation can represent that response without volumetric locking.
- Keep the validated material range in view when interpreting the structural result.

## Couple material and discretization choices

Hybrid formulations are often relevant for nearly incompressible response, but the exact element choice still depends on dimensionality, mesh quality, deformation mode, and requested outputs. Route exact Abaqus element names and API signatures through the release-specific evidence channels rather than memorizing them here.

Check element distortion and the pressure/volumetric response as deformation grows. A converged nonlinear increment does not by itself establish that the constitutive law or discretization is appropriate.

## Verification focus

Useful checks include reaction balance, monotonicity and symmetry when physically expected, deformation shape, strain-energy behavior, mesh sensitivity in high-gradient regions, and comparison with the test data or benchmark used to identify the material parameters.

Read [references/modeling-principles.md](references/modeling-principles.md) when the unresolved issue is finite-strain constitutive meaning, near-incompressibility, calibration coverage, or extrapolation rather than an Abaqus-specific setting. If the unresolved question is how to interpret `S`, `E`, `NE`, or `LE`, or how to compare FE strain with DIC/test strain, route to `geometric-nonlinear-fea` and its finite-strain output reference instead of duplicating the output conventions here.

## Official implementation reference

Use [references/abaqus-official-notes.md](references/abaqus-official-notes.md) as the official-reference index. For structural modeling, load `constitutive-models-and-compressibility.md` and/or `element-formulation-and-procedures.md`; load `calibration-and-test-data.md` only when the material fit itself is unresolved. Read [references/official-examples.md](references/official-examples.md) before using the supplied official script. The rubber-mount example is a compact reference for a polynomial hyperelastic material, axisymmetry, `nlgeom=ON`, and a hybrid element formulation. It is not a source of generic rubber parameters.

Use `geometric-nonlinear-fea` when the unresolved issue is the effect of changing geometry, load direction, or large rotations rather than the constitutive law itself.
