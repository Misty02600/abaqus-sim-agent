# Official examples: linear structural foundation

The shared official example pool keeps four basic representation anchors from the Abaqus Getting Started appendix:

- `scripts/gsi_frame_caemodel.py`, from `simagsa-c-appendixframe.htm`: 2D wire/truss-style structural representation and section assignment.
- `scripts/gsi_lug_caemodel.py`, from `simagsa-c-appendixlug.htm`: three-dimensional solid geometry, partitioning, regions, and meshing.
- `scripts/gsi_skewplate_caemodel.py`, from `simagsa-c-appendixskewplate.htm`: shell/plate representation under transverse loading.
- `scripts/gsi_crane_caemodel.py`, from `simagsa-c-appendixcrane.htm`: beam section/profile and beam-orientation workflow.

One complex-comprehensive reference is also retained:

- `scripts/pipe_cae_c3d20rsub_s4.py`, from `SIMACAEEXARefMap/simaexa-c-shellsolidpipe.htm`: shell global model plus a C3D20R solid submodel, useful for studying shell-to-solid submodeling/coupling and mixed representation. The official script also submits jobs and reads ODB data, so use it as a source reference rather than a template for the project's current execution boundary.

Use these as implementation/navigation anchors after the physical idealization is fixed. They come from Abaqus 2026 FD02 documentation and are syntax-checked during export only; they are not Abaqus 2024 runtime compatibility evidence or benchmark oracles.
