---
name: hyperelastic-calibration
description: "Interpret and prepare hyperelastic material calibration data: test modes, stress/strain data provenance, compressibility, model fitting, stability, and calibration-range checks. Use when the task is identifying or validating constitutive parameters rather than building the structural FE model."
---

# Hyperelastic Calibration

Use this skill when the main task is to identify, fit, compare, or validate a hyperelastic constitutive law from material data. The output of this work is material-model evidence for later FEA; it does not decide the geometry, boundary conditions, mesh, or structural procedure.

## Establish the data before fitting

- Identify the available deformation modes: uniaxial, equibiaxial, planar, volumetric, or another controlled test.
- Preserve the stress/strain measure, temperature, rate, specimen direction, preprocessing, and source of every curve.
- Do not infer missing curves or volumetric behavior from a material name such as rubber, PDMS, silicone, or Ecoflex.
- Keep test-derived parameters, literature parameters, and tutorial constants distinct.

## Fit for the intended deformation range

Choose candidate strain-energy forms according to the available test modes and the intended deformation range. Compare their predictions against all available curves rather than selecting a model from one scalar fit score.

Check extrapolation and material stability over the range the structural simulation will actually use. A fit that reproduces one test mode can still be unreliable in another mode or outside the calibration interval.

Compressibility is part of calibration. If volumetric data are unavailable, any assumed Poisson ratio, bulk modulus, or equivalent parameter remains an explicit modeling assumption.

## Hand-off to structural modeling

Report the selected model family, parameter values, units, source data, fitted range, unsupported extrapolation, and compressibility assumption. Use `hyperelastic-fea` when the remaining question is how those material data should be represented in the structural Abaqus model, including hybrid formulations and finite-deformation discretization.

Read the shared theory reference [../hyperelastic-fea/references/modeling-principles.md](../hyperelastic-fea/references/modeling-principles.md) when the unresolved issue is identifiability across deformation modes, near-incompressibility, or validity outside the calibration range.

Read the shared package reference [../hyperelastic-fea/references/calibration-and-test-data.md](../hyperelastic-fea/references/calibration-and-test-data.md) for Abaqus 2026 FD02 calibration semantics and [../hyperelastic-fea/references/constitutive-models-and-compressibility.md](../hyperelastic-fea/references/constitutive-models-and-compressibility.md) when volumetric behavior or model-family meaning is unresolved. These are not Abaqus 2024 runtime compatibility evidence.
