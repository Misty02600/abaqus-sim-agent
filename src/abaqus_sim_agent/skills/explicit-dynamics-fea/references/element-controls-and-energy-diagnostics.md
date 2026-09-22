# Explicit dynamics: element controls and energy diagnostics

Source pages: `SIMACAEELMRefMap/simaelm-c-sectioncontrol.htm` (*Section Controls*) and `SIMACAEOUTRefMap/simaout-c-exp-totalenergyoutput.htm` (*Abaqus/Explicit total energy output*), Abaqus 2026 FD02. This note records product semantics, not Abaqus 2024 API compatibility.

Use this reference when reduced-integration hourglass behavior, severe element distortion, or artificial-energy histories affect whether an Explicit result is trustworthy.

## Hourglass control is a numerical constraint

Reduced-integration elements can possess zero-energy hourglass modes. If those modes are excited, the mesh can deform severely without the corresponding physical strain energy. Abaqus provides several hourglass-control formulations to suppress this behavior.

Hourglass control improves numerical behavior by adding resistance to nonphysical modes; it does not prove that the mesh is sufficiently resolved. Inspect the deformed shape and the quantity of interest together with the energy diagnostics. Changing the hourglass formulation or scale factors is therefore a numerical-model change whose effect should be checked when it materially influences the answer.

`ALLAE` is the whole-model artificial strain energy associated with constraints used to remove singular modes such as hourglass modes and with certain shell drill constraints. It is part of `ALLIE` in the Abaqus/Explicit total-energy accounting, so a seemingly small `ALLKE/ALLIE` ratio alone can be misleading if artificial contributions are significant.

## Distortion control is not a repair for bad physics

For supported solid elements, distortion control uses penalty constraints to prevent nodes from moving far enough inward that an element becomes nonconvex or inverts. This can help analyses with severe compressive deformation, but the control itself can inhibit legitimate deformation modes or lock the mesh.

Distortion control cannot cure temporal instability, hourglass instability, or physically unrealistic deformation. If it activates strongly, first determine why the element is distorting: material response, mesh resolution, contact, loading rate, element formulation, and local geometry can all be responsible.

The whole-model variable `ALLDC` reports energy dissipated by distortion control. In some section-control combinations Abaqus also places associated viscous dissipation in `ALLAE`; interpret the requested energy variables in the context of the element controls actually used.

## No universal energy threshold

Do not turn `ALLAE/ALLIE`, `ALLDC/ALLIE`, or `ALLKE/ALLIE` into a universal pass/fail percentage. Their acceptable magnitude depends on the physical problem and the requested quantity of interest.

Use the histories diagnostically:

- inspect whether artificial or distortion-control energy grows where nonphysical deformation appears;
- compare deformed shape, contact behavior, reactions/momentum, and relevant physical energies;
- repeat with mesh or element/control changes when the response is sensitive;
- for quasi-static Explicit work, also vary loading time and mass scaling so low apparent kinetic energy is not mistaken for proof of a static solution.

A stable run that finishes without negative-volume errors is still not sufficient evidence of spatial, temporal, or physical accuracy.
