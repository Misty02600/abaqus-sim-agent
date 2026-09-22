# Official examples: Explicit dynamics

Primary canonical script:

- `scripts/gxi_stresswave_caemodel.py`
- source page `SIMACAEGSARefMap/simagsa-c-appendixwave.htm`
- key patterns: `ExplicitDynamicsStep`, Explicit `C3D8R/C3D6/C3D4`, short-duration stress-wave propagation.

Additional canonical scripts:

- `scripts/gxi_stiffplate_caemodel.py`, from `simagsa-c-appendixblast.htm`: blast pressure history on a stiffened shell plate with steel plasticity.
- `scripts/gxi_circuit_caemodel.py`, from `simagsa-c-appendixcircuit.htm`: circuit-board drop test using `ExplicitDynamicsStep` and Explicit general contact.
- `scripts/rivet_forming.py`, from `SIMACAEEXARefMap/simaexa-c-rivet.htm`: complex-comprehensive forming example with both CEL and Lagrangian modeling, plasticity, Explicit dynamics, and general contact.
- `scripts/cel_bottle_drop.py`, from `SIMACAEEXARefMap/simaexa-c-bottledrop.htm`: complex-comprehensive fluid-filled bottle impact using coupled Eulerian-Lagrangian modeling, general contact, plasticity, and damage.

Cross-reference:

- `scripts/gsi_dyncrane_caemodel.py` contains a distinct Explicit model branch in addition to the Standard modal-dynamics workflow.
- `scripts/gsi_channel_caemodel.py` contains a separate Explicit forming model in addition to its Standard forming model.
- `scripts/rigmultimech.py` builds closely related Standard implicit-dynamics and Explicit mechanism models, making the solver-procedure contrast unusually direct.

The exporter preserves source code and records provenance only. Use the Abaqus 2024 API/runtime evidence channels before relying on release-specific details.
