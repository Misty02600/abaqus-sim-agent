# Hyperelastic calibration and test data

Source page: `SIMACAEMATRefMap/simamat-c-hyperelastic.htm`, sections on test-data calibration, experimental tests, smoothing, prediction, stability, and fit improvement; Abaqus 2026 FD02.

Use this reference for the `hyperelastic-calibration` task entry.

## Preserve the test modes

Abaqus can use homogeneous deformation data from uniaxial, equibiaxial, planar, and volumetric tests. Keep each curve's stress/strain measure, units, temperature, rate, preprocessing, specimen direction, and source attached to the calibration record.

Multiple deformation modes constrain the constitutive response more strongly than one curve. A fit that matches a uniaxial test can still extrapolate poorly in biaxial, planar, or volumetric deformation.

## Fitting and prediction

Treat smoothing as preprocessing that can change the fitted material response; preserve what was changed. Compare the selected model prediction against all available test modes and against the strain range expected in the structural simulation.

Check material stability over the intended deformation range. A low fitting error does not establish stable or physically credible extrapolation outside the measured range.

## Calibration hand-off

Record the selected potential, coefficients, units, source curves, fitted range, unsupported extrapolation, and compressibility assumption. Structural modeling should consume that evidence rather than refit or invent material parameters during model construction.
