# Finite-strain stress and strain output measures

Source baseline: local Abaqus 2026 FD02 conventions, Standard output-variable guidance, and finite-strain theory pages. This note records output semantics, not Abaqus 2024 runtime compatibility.

Use this reference when a large-deformation result is being interpreted, especially when `S`, `E`, `NE`, or `LE` is compared with an experiment, DIC field, analytical expression, or another solver.

## Stress output `S`

Abaqus reports stress as **Cauchy stress**, also called true stress: force per current area. Treat normal stress output `S` as Cauchy stress. Kirchhoff stress and second Piola-Kirchhoff stress appear in large-deformation theory and constitutive derivations, but they are not the meaning of ordinary reported `S`.

## There is no single finite-strain “true strain”

At large deformation different strain measures give different numerical values for the same physical deformation. Abaqus explicitly does not define one uniquely preferred “true strain.” Do not use the phrase “true strain” alone when the required measure matters; name the measure and output variable.

- `LE` is logarithmic strain.
- `NE` is nominal strain.
- `E` is the integrated total-strain output used by Abaqus/Standard in the contexts where it is available. Do not assume that `E` is a universal synonym for Green-Lagrange strain; its meaning depends on formulation and output context.

For a one-dimensional stretch ratio `lambda`, nominal strain is `lambda - 1` and logarithmic strain is `ln(lambda)`. Their difference becomes material as stretch grows, so matching only the component name is insufficient.

## Output destination changes the default

For Abaqus/Standard, the default total-strain output to `.dat` and `.fil` is `E`. For geometrically nonlinear analyses using element formulations that support finite strain, `E` is not available to the ODB and `LE` is the default ODB strain measure.

For Abaqus/Explicit, `LE` is the default strain output, `NE` can be requested, and integrated total strain `E` is not available.

Therefore, do not write a generic rule such as “Abaqus defaults to `E`” without naming the product, formulation, and output destination.

## Comparing with DIC or test data

Before comparing strain values, identify the experimental strain definition and make the FE result use the same measure. DIC software may report engineering/nominal, logarithmic, Green-Lagrange, or another configured strain measure. Also align the component direction or coordinate frame used for the comparison.

If the experiment reports displacement or stretch rather than a defined strain tensor, compare that primary quantity directly when practical; otherwise convert both sides to one stated strain measure. A numerical mismatch between `LE` and an engineering-strain curve can be a measure mismatch rather than a modeling error.

## Source pages

- `SIMACAEMODRefMap/simamod-c-conventions.htm` — Abaqus stress/strain conventions and `E`/`NE`/`LE` meanings.
- `SIMACAEOUTRefMap/simaout-c-stdoutputvar.htm` — Standard output availability and `.dat`/`.fil`/ODB defaults.
- `SIMACAETHERefMap/simathe-c-strainmeas.htm` — finite-strain measure theory and small-strain equivalence.
- `SIMACAETHERefMap/simathe-c-stressmeas.htm` — Cauchy, Kirchhoff, and second Piola-Kirchhoff stress measures.
