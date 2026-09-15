# Evidence for `linear-structural-fea-verification`

This reference records the sources behind verification terminology and the bounded checks used by the skill. It is not a task-specific oracle and is not evidence that a user model represents physical reality.

## Verification and validation terminology

- NAFEMS, [Code Verification v Solution Verification](https://www.nafems.org/blog/posts/code-verification-v-solution-verification/): used to distinguish code verification from solution verification and to place mesh refinement within solution verification for a specific computational model.
- NAFEMS, [Code Verification Exemplars in Computational Solid Mechanics (R0135)](https://www.nafems.org/publications/resource_center/r0135/): public resource page used only for the organization of code-verification exemplar topics such as reference solutions, mesh refinement, QoIs, and observed order of accuracy.
- NAFEMS glossary, [verification/validation terminology](https://www.nafems.org/publications/glossary/terms_s-z/): used for the intended-use relationship in validation.

The project has not acquired the paid R0135 body. Do not infer benchmark values, tolerances, hidden reference solutions, or detailed procedures from the public page.

## Structural sanity checks

- A. F. Bower, *Applied Mechanics of Solids*, Chapters 1.1 and 7.1: supports comparing the numerical result with the mechanics problem, load path, boundary conditions, and quantities being interpreted.
- K. J. Bathe, MIT OpenCourseWare, *Finite Element Procedures for Solids and Structures — Linear Analysis*: supports the linear-analysis framework used for proportional scaling and linear structural reference comparisons.

Static equilibrium, independent-reference comparison, proportional scaling, symmetry, and deformation-mode checks are used as bounded consistency checks. No one of them proves physical validity by itself.

## Project acceptance boundary

The repository's task-specific host verifier/evaluator owns CodingRun acceptance, artifact freshness, execution provenance, and case-specific numerical/physics checks. This skill describes engineering verification reasoning only and cannot authorize the Agent to mark that host contract as passed.

## Related project audit

- `docs/scratch/linear-structural-fea-source-topic-matrix.md`
- `docs/scratch/linear-structural-fea-evidence-bindings.md`
- `docs/scratch/linear-structural-fea-routing.md`
