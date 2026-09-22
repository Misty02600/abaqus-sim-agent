# Official examples: structural dynamics

- `scripts/gsi_dyncrane_caemodel.py`, from `simagsa-c-appendixdyncrane.htm`: contains a Standard `FrequencyStep` followed by `ModalDynamicsStep`, plus a separate Explicit model branch. This makes it useful for comparing procedure choices without treating the two solvers as interchangeable.
- `scripts/gsi_pipe_caemodel.py`, from `simagsa-c-appendixpiping.htm`: nonlinear/static loading followed by `FrequencyStep`, then a restart continuation. It is a useful preload/procedure-sequencing example.
- `scripts/rigmultimech.py`, from `SIMACAEEXARefMap/simaexa-c-crank.htm`: crank mechanism built first with Standard `ImplicitDynamicsStep(..., nlgeom=ON)` and then as a copied Explicit model. It is a topic-typical comparison case for procedure choice, rigid bodies, and connectors.

These scripts are canonical in the shared official pool. Read only the branch relevant to the current analysis question; do not copy an embedded workflow wholesale.
