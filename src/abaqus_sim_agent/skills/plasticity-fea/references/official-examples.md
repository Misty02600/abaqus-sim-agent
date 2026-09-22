# Official examples: plasticity

Primary canonical script:

- `scripts/gsi_plasticlug_caemodel.py`
- source page `SIMACAEGSARefMap/simagsa-c-appendixplastlug.htm`
- demonstrates `Material.Plastic(...)` with a three-dimensional lug and a static load step.

Cross-reference:

- `scripts/gsi_lap_joint_caemodel.py`
- combines tabulated plasticity with frictional contact and `nlgeom=ON`; use it to study mechanism interaction, not as the first isolated plasticity example.
- `scripts/gxi_stiffplate_caemodel.py`: Explicit blast loading with tabulated steel plasticity and shell sections.
- `scripts/gsi_channel_caemodel.py`: channel forming with plasticity, frictional contact, large geometry change, and both Standard/Explicit model branches.
- `scripts/gsi_channel_caemodel_spring.py`: springback continuation after forming; useful for imported/deformed-state continuation and residual plastic deformation.
- `scripts/pillar_fastener_std.py`, from `SIMACAEEXARefMap/simaexa-c-bucklespotweld.htm`: plastic collapse coupled with large deformation, finite-sliding contact, and discrete fasteners; retained as a complex-comprehensive cross-reference.
- `scripts/rivet_forming.py`, from `SIMACAEEXARefMap/simaexa-c-rivet.htm`: plastic forming in an Explicit/CEL workflow with general contact; use after isolated constitutive examples.
- `scripts/cel_bottle_drop.py`, from `SIMACAEEXARefMap/simaexa-c-bottledrop.htm`: HDPE plasticity and ductile damage inside a fluid-filled bottle impact model; retained as a comprehensive application rather than a first plasticity example.

These scripts come from Abaqus 2026 FD02 documentation. Export preserves the original tutorial code and records hashes; no 2024 runtime compatibility follows from that export alone.
