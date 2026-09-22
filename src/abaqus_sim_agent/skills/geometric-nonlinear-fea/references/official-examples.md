# Official examples: geometric nonlinearity

Primary canonical script:

- `scripts/gsi_nlskewplate_caemodel.py`
- source page `SIMACAEGSARefMap/simagsa-c-appendixnlskew.htm`
- key pattern: shell model with `StaticStep(..., nlgeom=ON)`.

Useful cross-references already owned by other topics:

- `scripts/gsi_mount_caemodel.py`: hyperelastic axisymmetric mount with `nlgeom=ON`.
- `scripts/gsi_lap_joint_caemodel.py`: frictional contact, plasticity, and `nlgeom=ON` in one model.
- `scripts/gsi_pipe_caemodel.py`: nonlinear static preload followed by frequency extraction/restart workflow.
- `scripts/gsi_channel_caemodel.py`: Standard forming branch uses `nlgeom=ON` and contact while the same source script also builds an Explicit branch.
- `scripts/gsi_channel_caemodel_spring.py`: springback continuation uses a geometrically nonlinear static step after forming.
- `scripts/snubber_model.py`: geometrically nonlinear mechanism/contact model with rigid bodies and connectors.
- `scripts/pillar_fastener_std.py`: collapse/buckling application combining `nlgeom=ON`, plasticity, fasteners, and contact.
- `scripts/alfano_2d_model.py`: cohesive delamination benchmark with `nlgeom=ON`; canonical ownership is `interface-cohesive`, because cohesive damage is broader than ordinary surface contact.

Keep one canonical copy of each script in the official pool instead of duplicating it across skills.
