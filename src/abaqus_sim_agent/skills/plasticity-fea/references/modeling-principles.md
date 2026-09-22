# Plasticity modeling principles

Theory anchors: Simo & Hughes, *Computational Inelasticity*; Belytschko et al., *Nonlinear Finite Elements for Continua and Structures*. This note records reusable constitutive reasoning. Abaqus-specific yield models, table syntax, procedure support, defaults, and release compatibility belong to the package's official references and release-specific evidence.

Use this reference when the unresolved issue is the physical meaning or adequacy of a plasticity idealization rather than how to enter it in Abaqus.

## Separate the ingredients of a plasticity model

A classical rate-independent plasticity model needs distinct statements about the yield surface, flow rule, and hardening law. They answer different questions: when plastic loading starts, in which strain direction plastic flow develops, and how the admissible stress domain evolves with history.

Do not infer all three from a single monotonic tensile curve. A curve can constrain one loading path while leaving anisotropy, reverse loading, multiaxial response, and cyclic evolution unidentified.

## Preserve history and internal variables

Plastic response is path dependent. The current stress depends on the loading history through internal variables such as accumulated plastic strain or back-stress-like quantities. Two analyses with the same final external load can reach different states after different preload, unloading, reversal, thermal, or contact histories.

When a structural result depends on yielding, preserve the actual sequence of increments and boundary-condition changes. Do not replace a history-dependent problem by a final-load snapshot unless that reduction is justified.

## Distinguish test data from constitutive data

Experimental stress-strain measurements are observations; the constitutive model requires model-specific measures and variables. Converting a uniaxial engineering curve into true stress and the required plastic strain is part of model identification, not clerical formatting.

Check whether the intended analysis exceeds the calibrated ranges of strain, stress state, temperature, or rate. Extrapolation beyond those ranges is an assumption and should be visible in the task evidence.

## Local response can become mesh sensitive

Classical plasticity can produce strong localization when the constitutive tangent softens or when damage/instability is added. Once deformation localizes into a narrowing zone, peak local strain may cease to converge under ordinary mesh refinement. A smaller element can then report a larger local strain without representing a more accurate material prediction.

Define the quantity of interest accordingly. Global force-displacement response, plastic-zone extent, averaged measures, or a regularized constitutive model may be more meaningful than a pointwise maximum when localization controls the result.

## Numerical solution is part of verification, not material identification

Incremental plasticity is normally solved through local constitutive updates coupled to global equilibrium iterations. Failure to converge can come from the structural problem, the constitutive tangent, the load path, contact, or geometry. Do not change material data merely to improve convergence.

Before trusting a complex structure, reproduce the expected elastic branch, yield onset, hardening, unloading/reloading, and any relevant reversal in a material-point or one-element path. Then verify the structural response with reactions, plastic-zone evolution, energy/dissipation where relevant, and mesh sensitivity.
