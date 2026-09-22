# Cohesive-element thickness and connectivity

Source pages: `SIMACAEELMRefMap/simaelm-c-cohesiveoverview.htm`, `SIMACAEELMRefMap/simaelm-c-cohesiveelem.htm`, and `SIMACAEELMRefMap/simaelm-c-cohesivebehavior.htm`, Abaqus 2026 FD02.

Use this reference only when cohesive elements are the selected interface representation.

## Thickness meaning

Keep geometric thickness distinct from constitutive thickness. Cohesive formulations use a thickness convention to relate nominal strains/separations to traction response; changing that convention can change the interpretation of stiffness and material parameters even when the visible layer is very thin.

Zero-thickness or very thin idealizations therefore need an explicit statement of how thickness is represented in the constitutive data. Do not silently transfer parameters calibrated under one thickness convention to another.

## Orientation and connection to bulk material

Confirm the cohesive element normal/tangential directions because traction components and damage modes depend on that orientation. Check element connectivity and face ordering against the neighboring bulk regions so opening and shear are interpreted in the intended directions.

The cohesive layer must transfer load into both adjacent bodies. A conforming mesh can provide direct connectivity; a nonconforming interface may require a deliberate tie or other compatible connection strategy. Avoid accidental free faces as well as duplicate constraints that overconstrain the same degrees of freedom.

## Checks

Before a full structure, inspect a small cohesive patch or coupon for thickness/orientation, displacement continuity, intact stiffness, expected opening/shear sign, and reaction balance. If ties or nonmatching meshes are used, verify that the connection does not dominate the measured interface compliance or failure path.
