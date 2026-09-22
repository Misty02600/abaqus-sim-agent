# Official example: rubber mount

- Canonical script: `scripts/gsi_mount_caemodel.py` in the optional official structural example pool.
- Teaching tier: `basic-representative`.
- Local source page: `SIMACAEGSARefMap/simagsa-c-appendixmount.htm`, Abaqus 2026 FD02 documentation.
- Primary value: polynomial `Hyperelastic`, axisymmetric geometry, `StaticStep(..., nlgeom=ON)`, and `CAX4H` alongside a non-hybrid comparison region.
- Cross-topics: geometric nonlinearity, axisymmetric modeling, hybrid elements.

The source script is retained intact, including tutorial/GUI operations. It has only been syntax-checked when exported; the 2026 documentation is not evidence of Abaqus 2024 runtime compatibility. Adapt any execution to the project's CAE → Solver → ODB stage boundary.
