# Contact mechanical behavior

Source page: `SIMACAEITNRefMap/simaitn-c-contactmechanical.htm` (*Defining the Mechanical Contact Properties*), Abaqus 2026 FD02.

Use this reference after the interacting regions and contact route are known, when the unresolved decision is the constitutive behavior across the interface.

## Normal behavior

Normal contact behavior governs how the interface carries compression, opens, and treats clearance or overclosure. Pressure-overclosure choices represent different interface assumptions and should come from the physical problem or a justified numerical idealization.

Do not infer a pressure-overclosure law, initial clearance, or allowed separation from geometry alone. Preserve the intended initial state and the meaning of any penalty or softened response.

## Tangential behavior

Tangential contact behavior is a separate decision from normal contact. Frictionless contact is the default surface-contact behavior when no friction model is supplied; adding friction requires a coefficient or a more detailed tangential law with provenance.

Do not invent friction, interface damping, or other tangential parameters to make a model converge. If a parameter materially affects the requested response, keep its source, units, dependence on pressure/slip/rate/temperature when applicable, and calibration range attached to the model.

## Cohesive routing

Bonded traction-separation behavior can be expressed within the contact framework, but progressive cohesive response has its own intact stiffness, initiation, evolution, and post-failure questions. Route those decisions to `interface-cohesive-fea` rather than treating them as ordinary frictional contact.

## Checks

Inspect contact pressure/normal force, clearance or penetration, tangential traction, slip/stick state, frictional work when relevant, and global reaction balance. Check sensitivity to the chosen interface law when it is an idealization rather than directly measured behavior.
