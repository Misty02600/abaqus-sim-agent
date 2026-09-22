# Cohesive traction-separation and damage

Source pages: `SIMACAEELMRefMap/simaelm-c-cohesivebehavior.htm` and `SIMACAEITNRefMap/simaitn-c-cohesivebehavior.htm`, Abaqus 2026 FD02.

Use this reference when the cohesive representation is known but the intact response, damage sequence, mixed-mode behavior, or post-degradation mechanics are unresolved.

## Intact stiffness

The undamaged traction-separation response defines normal and tangential interface compliance before damage. Too little stiffness can add artificial structural compliance; an excessively stiff penalty-like response can hurt conditioning in Standard or reduce the stable time increment in Explicit.

Choose stiffness from measured/interface physics or a documented penalty argument and verify its effect on the requested structural quantity. Do not infer stiffness from an adhesive trade name or from bulk Young's modulus without a justified relation and thickness interpretation.

## Initiation and evolution are different decisions

Damage initiation determines when degradation is allowed to begin. Damage evolution determines how stiffness/traction subsequently degrades and the separation or energy required to progress toward failure. An initiation criterion by itself does not necessarily produce mechanical degradation; in supported cohesive-contact usage it can be evaluated for output without an evolution law changing the response.

Evolution may be displacement/separation based or energy based and may depend on mode mix. Preserve the exact definitions and units of strengths, fracture energies, mode-mix parameters, and any rate or temperature dependence.

## Compression and post-failure contact

Do not interpret pure compressive contact as tensile/shear cohesive damage. Compression, reopening, sliding, and penetration prevention after substantial degradation depend on how cohesive and contact behavior are combined in the chosen representation.

## Checks

Use a simple opening, shear, or mixed-mode coupon to check intact compliance, peak traction/load, damage onset, degradation path, dissipated energy for energy-based evolution, and post-degradation contact. Then assess mesh sensitivity and mode-mix response before relying on a complex debonding path.
