# Metal plasticity: stress-strain data preprocessing

Source page: `SIMACAEMATRefMap/simamat-c-plastic.htm`, section **Stress and Strain Measures**; Abaqus 2026 FD02. This note records material-table semantics, not Abaqus 2024 API compatibility.

Use this reference when measured uniaxial tensile data are available as nominal/engineering stress and strain and must be converted into a classical metal-plasticity hardening table.

## Conversion chain for uniform uniaxial data

For the isotropic uniaxial conversion described by the Abaqus documentation, start from nominal stress `sigma_nom` and nominal strain `epsilon_nom` and compute

`sigma_true = sigma_nom * (1 + epsilon_nom)`

`epsilon_log_total = ln(1 + epsilon_nom)`

The plastic strain entered in the hardening table removes the elastic part:

`epsilon_log_pl = epsilon_log_total - sigma_true / E`

The resulting table pairs **true (Cauchy) stress** with **logarithmic plastic strain**. Do not enter total logarithmic strain or raw engineering strain as the plastic-strain column.

## First hardening point

The first plastic-data pair corresponds to initial yield and therefore has plastic strain `0`. Later pairs describe the evolution of yield stress with accumulated plastic strain for the chosen hardening law.

If the available data do not identify the onset of yielding cleanly, that is a calibration/data-quality question. Do not silently force an arbitrary measured point to zero plastic strain without recording the chosen yield definition.

## Applicability and limits

- Preserve the original test units, temperature, strain-rate condition, specimen direction, test method, and all preprocessing choices with the derived table.
- The simple nominal-to-true conversion assumes a uniform uniaxial state representative of the specimen gauge section. Once necking or strong localization develops, the measured force and gauge-length extension no longer define a unique local true stress-strain curve through these formulas alone.
- Do not extrapolate a post-necking hardening law by blindly continuing this conversion. Use an appropriate inverse-calibration, local-measurement, or constitutive-identification method when post-necking behavior controls the analysis.
- Rate dependence, anisotropy, cyclic hardening, damage, and thermal coupling remain separate constitutive choices. Preprocessing the stress-strain measures does not establish those model assumptions.

## Verification after preprocessing

Plot the derived true-stress versus plastic-strain table before using it. Check that plastic strain begins at zero, is nondecreasing along the intended monotonic branch, and that stress/strain units are consistent with the elastic modulus used in the subtraction. Keep the raw measurements and the transformation procedure so the Abaqus table can be traced back to the test data.
