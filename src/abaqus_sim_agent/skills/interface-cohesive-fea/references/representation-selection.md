# Cohesive representation selection

Source pages: `SIMACAEELMRefMap/simaelm-c-cohesiveoverview.htm` (*Modeling with Cohesive Elements*) and `SIMACAEITNRefMap/simaitn-c-cohesivebehavior.htm` (*Surface-Based Cohesive Behavior*), Abaqus 2026 FD02.

Use this reference before defining a cohesive law when the unresolved question is whether the interface should be represented by cohesive elements or by surface-based cohesive behavior.

## Cohesive elements

Cohesive elements make the adhesive/interface a discretized region. This is useful when layer thickness, element topology, orientation, field output in the layer, or an explicit interface mesh matters to the problem. The representation therefore introduces element connectivity and thickness/orientation decisions in addition to the constitutive law.

## Surface-based cohesive behavior

Surface-based cohesive behavior lives inside the contact/interaction framework and does not require a separate cohesive-element layer. It is suitable when the bond is naturally represented between surfaces and when contact behavior before, during, or after degradation is part of the interaction definition.

Depending on the configured eligibility, a surface interaction may represent an initially bonded interface or allow bonding after surfaces encounter each other. Preserve that physical distinction; do not assume all surface-based cohesive models have the same bonding history.

## Selection rule

The two representations can share traction-separation concepts, but their topology, discretization, contact handling, thickness meaning, and available controls differ. Do not switch between them solely because one API is easier to script.

Choose from the physical role of the layer/interface, required outputs, mesh strategy, expected contact after degradation, and whether an explicit adhesive region is part of the model.
