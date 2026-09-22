# Mass scaling and quasi-static Explicit analysis

Source page: `SIMACAEANLRefMap/simaanl-c-massscaling.htm` (*Mass Scaling*), Abaqus 2026 FD02.

Use this reference when mass scaling is proposed or when Explicit is used as a numerical route to a nominally static response.

## What mass scaling changes

Mass scaling increases selected element/contact mass to increase the stable time increment. It changes inertia and therefore is not numerically free. Abaqus provides fixed and variable approaches, direct scale factors, target element-by-element stable increments, local/global application, and contact-related mass scaling.

Record where scaling is applied, its target or factor, and the resulting mass change. First determine whether the small stable increment comes from necessary physics or from avoidable mesh/pathology.

## Quasi-static use

For a nominally static problem, the artificial event duration and added mass change the dynamic time scale. Apply loading smoothly and verify that inertial effects remain small enough for the requested structural response.

No single kinetic/internal-energy percentage is a universal acceptance rule. Inspect the energy histories together with deformation mode and reaction/load response, and repeat with slower loading or less mass scaling when the quasi-static claim matters.

## Dynamic use

Mass scaling can also appear in genuinely dynamic work, but the admissible change depends on sensitivity of the actual dynamic quantity of interest. Do not transfer a quasi-static acceptance argument to impact or wave-propagation problems.
