# Metal plasticity: yield, flow, and hardening

Source page: `SIMACAEMATRefMap/simamat-c-metalplastic.htm`, sections on yield surfaces, hardening, and flow rule; Abaqus 2026 FD02.

Use this reference when selecting or interpreting the classical plasticity model family.

## Yield surface

The classical metal-plasticity model combines a yield surface with an associated flow rule and a hardening description. A Mises surface represents isotropic yielding; Hill-type options represent anisotropic yielding through directional yield-stress ratios.

Do not choose anisotropy, perfect plasticity, or a particular hardening law from convenience. Each represents a different statement about the material response.

## Hardening and loading path

Perfect plasticity holds the yield level fixed after yielding. Isotropic hardening expands the yield surface and is commonly suited to monotonic loading when supported by data. Kinematic hardening translates the yield surface and is relevant when load reversal or cyclic response matters.

If the requested response includes substantial reversal, ratcheting, or cyclic stabilization, a monotonic isotropic curve is not sufficient evidence merely because it reproduces the first loading branch.

## Checks

Track yield onset, equivalent plastic strain, load-displacement response, and unloading/reloading behavior when relevant. Validate the constitutive choice first with a simple material-point or one-element path before relying on a complex structural model.
