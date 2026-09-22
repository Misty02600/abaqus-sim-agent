# Abaqus official references: linear structural foundation

Source baseline: local Abaqus 2026 FD02 documentation. These files summarize product semantics for modeling decisions; they do not establish Abaqus 2024 API compatibility.

Load only the reference that matches the unresolved decision:

- [static-analysis-procedure.md](static-analysis-procedure.md) — what a static stress step means, when the baseline is truly linear, and which step-level assumptions can introduce nonlinearity.
- [beam-element-selection.md](beam-element-selection.md) — beam idealization, slender versus shear-flexible behavior, section/orientation data, and open-section or hybrid considerations.
- [shell-element-selection.md](shell-element-selection.md) — conventional versus continuum shells, thin/thick and finite/small-strain choices, integration, and shell-specific modeling issues.
- [solid-element-selection.md](solid-element-selection.md) — continuum element order, integration, topology, hybrid/incompatible formulations, and three-dimensional representation tradeoffs.

The source pages are indexed by `import_official_structural_texts.py`. Exact element names, API signatures, defaults, and Abaqus 2024 compatibility continue to use release-specific evidence.
