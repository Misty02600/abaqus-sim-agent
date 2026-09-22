# Contact formulation and topology

Source page: `SIMACAEITNRefMap/simaitn-c-contactoverview.htm` (*About Contact Interactions*), Abaqus 2026 FD02.

Use this reference when the unresolved decision is which regions can contact and how that interaction should be represented in Abaqus.

## Separate interaction topology from interface behavior

Define the candidate contacting surfaces or regions before assigning normal or tangential properties. The geometry/assignment of the interaction and the mechanical law acting across it are separate modeling decisions.

Abaqus provides multiple contact routes whose availability and behavior depend on the analysis product and procedure. General contact, contact pairs, and contact elements are not interchangeable labels: they differ in how the interaction network is defined and controlled. Choose the route from the actual contact network, expected motion, dimensional representation, and required controls.

Do not infer contact simply because two part surfaces coincide in CAD. Confirm which surfaces are allowed to engage, whether self-contact is relevant, and whether the interaction should exist from the start or only after surfaces approach each other.

## Solver and motion assumptions

Preserve the distinction between Abaqus/Standard and Abaqus/Explicit when selecting contact capabilities and controls. Also preserve any small-sliding or finite-sliding assumption where the chosen contact formulation exposes that distinction; the expected relative motion is a physical/modeling input.

## Checks

Verify the assigned regions, normals/orientations where relevant, expected open/contact states, and whether the selected topology can represent the intended separation and sliding. A numerically converged analysis with the wrong interaction network is still the wrong model.
