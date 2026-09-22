# Abaqus official notes: geometric nonlinearity

Source baseline: local Abaqus 2026 FD02 geometric-nonlinearity, conventions, output, and finite-strain theory guidance. These notes summarize product semantics, not Abaqus 2024 API compatibility.

Use this file for the baseline meaning and history behavior of geometric nonlinearity. For interpretation of `S`, `E`, `NE`, `LE`, ODB strain defaults, or experiment/FE strain comparisons, continue with [finite-strain-output-measures.md](finite-strain-output-measures.md). For eigenvalue buckling, initial imperfections, unstable collapse, postbuckling, modified Riks, proportional loading, or LPF, continue with [instability-buckling-and-path-following.md](instability-buckling-and-path-following.md).

## What `nlgeom` changes

Geometric nonlinearity is needed when changes in geometry materially alter equilibrium: large rotations or deflections, configuration-dependent load directions, membrane stiffening, stress stiffening, or other effects where the deformed configuration changes the structural response. Large strain is one possible consequence, not the only trigger.

In Abaqus/Standard general steps, geometric nonlinearity is an explicit step choice. In Abaqus/Explicit it is part of the normal formulation. Once geometric nonlinearity has been introduced in a Standard analysis history, later general steps continue from that nonlinear configuration.

## Geometry affects more than coordinates

With geometric nonlinearity active, element directions and structural stiffness can evolve with deformation. Shell, beam, and truss local directions follow their kinematics; load-stiffness and membrane/axial-stiffness effects can materially change transverse response and stability.

This is why a problem with linear elastic material data can still be strongly nonlinear.

## Separate nonlinear axes

Do not use this topic as a catch-all `nonlinear` switch. Material nonlinearity belongs to the constitutive model; contact belongs to the interaction definition. A model may need all three at once, but each requires independent input and verification.

## Verification checks

Compare small-load response with a linearized solution when possible, then inspect deformation shape, load-displacement path, rotations, reaction balance, and sensitivity to increment size/solution controls. For stability problems, use the dedicated instability reference to keep the perturbation result, imperfection definition, and nonlinear equilibrium path distinct.

The baseline sources are `SIMACAEGSARefMap/simagsa-c-nlngeomnonlin.htm` and `SIMACAECAERefMap/simacae-t-simstepmannlgeom.htm`. Finite-strain output semantics additionally use `SIMACAEMODRefMap/simamod-c-conventions.htm`, `SIMACAEOUTRefMap/simaout-c-stdoutputvar.htm`, `SIMACAETHERefMap/simathe-c-strainmeas.htm`, and `SIMACAETHERefMap/simathe-c-stressmeas.htm`. Continuum kinematics and nonlinear finite-element linearization should be supplemented from Bathe, Bonet & Wood, or Belytschko rather than expanded by inventing Abaqus-specific rules.
