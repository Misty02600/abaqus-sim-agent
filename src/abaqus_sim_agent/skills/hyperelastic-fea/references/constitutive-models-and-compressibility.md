# Hyperelastic constitutive models and compressibility

Source page: `SIMACAEMATRefMap/simamat-c-hyperelastic.htm`, sections on compressibility, isotropy, strain-energy potentials, and material definition; Abaqus 2026 FD02.

Use this reference when choosing or interpreting the hyperelastic model family or volumetric response.

## Constitutive scope

Abaqus rubberlike hyperelasticity represents isotropic nonlinear elastic response at finite strain through a strain-energy potential. The documentation provides multiple potential families, including Arruda-Boyce, Marlow, Mooney-Rivlin, Neo-Hookean, Ogden, polynomial/reduced-polynomial, Valanis-Landel, van der Waals, and Yeoh forms.

Model-family choice and coefficients must come from material evidence. The presence of large deformation or `nlgeom=ON` does not by itself identify a hyperelastic material law.

## Compressibility

Compressibility is part of the constitutive definition, not just a numerical setting. Nearly incompressible rubberlike materials can be very sensitive to the assumed volumetric response in confined or multiaxial deformation.

If volumetric test data are unavailable, any Poisson ratio, bulk modulus, or equivalent `D` parameter remains an explicit modeling assumption. Do not infer it from labels such as rubber, silicone, PDMS, or Ecoflex.

Abaqus/Standard and Abaqus/Explicit do not treat the incompressible continuum case identically. Preserve the solver/procedure when interpreting a material definition and consult the formulation reference before transferring assumptions between solvers.
