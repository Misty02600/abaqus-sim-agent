# Third-party UI reuse

This web UI reuses a small set of presentational code and assets from Pi Web.

- Upstream repository: `D:/Projects/abaqus-agent/references/pi-web`
- Audited upstream commit: `135517b26c9f74a1a796569c3c8d2a9cf65b3b43`
- Upstream license: MIT; preserved as `third-party/pi-web-LICENSE`

Reused or adapted source:

- `components/FileIcons.tsx` -> `src/FileIcons.tsx`
  - Kept the Catppuccin icon lookup/rendering structure.
  - Adapted imports/formatting for this Vite project.
  - Added Abaqus-specific `.inp` and `.odb` mappings.
- `components/ThinkingIcon.tsx` -> `src/ThinkingIcon.tsx`
  - Kept the SVG geometry.
  - Moved active-state colors/effects into this project's CSS tokens.
- `components/TurnWrittenFiles.tsx` -> `src/TurnWrittenFiles.tsx`
  - Kept the host-fact-only file chips and Catppuccin icon treatment.
  - Replaced Pi session records with the current tool summary's structured artifact changes.
  - Opens only files already present in the current host snapshot.
- `app/globals.css`
  - Reused the dark visual-token values and Catppuccin mask treatment as design input for `src/style.css`.
  - The current product remains dark-theme only; Pi's theme runtime was not imported.

Assets:

- `public/icons/catppuccin/**` was copied into `public/icons/catppuccin/**`.
- The Catppuccin asset license is preserved at `public/icons/catppuccin/LICENSE`.

The product does not import or read the local `references/pi-web` checkout at runtime.
