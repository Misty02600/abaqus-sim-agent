# Contact diagnostics in Abaqus/Explicit

Source page: `SIMACAEITNRefMap/simaitn-c-expcontacttrouble.htm` (*Troubleshooting Contact Analyses in Abaqus/Explicit*), Abaqus 2026 FD02.

Use this reference when an existing Explicit contact model shows penetration, force spikes, unstable-looking local response, or unexpectedly high cost.

## Inspect initialization and penetration

Check initial penetrations/overclosures, surface definitions, normals/orientations where relevant, constraints, and contact assignments before tuning contact controls. Distinguish intended geometric interference from accidental overlap caused by meshing or assembly placement.

During the run, inspect whether penetrations and contact forces are localized to the expected interface and whether their magnitude is consistent with the mesh, material stiffness, and event time scale.

## Couple contact diagnosis to Explicit time resolution

Contact stiffness, very small elements, and local high-frequency response can interact with the stable time increment and computational cost. A small stable increment is evidence about the discretized system, not by itself evidence that the contact law is physically wrong.

Likewise, a stable run does not establish temporal accuracy. Check contact force/work together with kinetic/internal energy, momentum or reactions as appropriate, mesh resolution, and the time scale of impact or sliding.

## Avoid numerical masking

Do not use mass scaling, excessive damping, or softened contact merely to hide a bad local mesh or incorrect initial contact state. If such controls are physically or numerically justified, record their extent and verify that the requested quantities are insensitive to a reasonable reduction in the intervention.
