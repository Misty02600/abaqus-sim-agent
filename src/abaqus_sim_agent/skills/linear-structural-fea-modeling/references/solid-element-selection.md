# Solid continuum element selection

Source page: `SIMACAEELMRefMap/simaelm-c-solidcont.htm` (*Solid (Continuum) Elements*), Abaqus 2026 FD02.

Use this reference when the model requires a continuum representation and the unresolved issue is interpolation order, integration, topology, incompressibility, or another solid-element formulation choice.

## Main choices

- Choose first- versus second-order interpolation from the deformation mode, mesh topology, contact needs, and accuracy requirements.
- Choose full versus reduced integration with awareness of locking, hourglass modes, bending performance, and computational cost.
- Prefer brick/quadrilateral topology when it can represent the geometry cleanly; triangular/tetrahedral families have their own accuracy and formulation tradeoffs and should not be selected only because meshing is easier.
- Hybrid formulations are relevant when near-incompressibility or another pressure constraint makes a pure displacement formulation unsuitable.
- Incompatible-mode and other specialized formulations address specific deformation deficiencies; use them for the documented mechanical reason rather than as generic accuracy upgrades.

## Representation and mesh checks

A continuum model resolves the three-dimensional stress field only to the accuracy supported by its mesh and formulation. Refine around bending, contact, holes, fillets, interfaces, and other high-gradient regions according to the requested quantity of interest.

Check deformation mode, reaction balance, stress/strain gradients, element distortion, and mesh convergence. Exact element labels, section-control arguments, and release behavior remain Abaqus 2024-specific evidence.
