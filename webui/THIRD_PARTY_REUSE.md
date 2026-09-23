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
- `components/ChatInput.tsx` -> `src/Composer.tsx`, `src/style.css`
  - Adapted the single-row starting height, inline send action, 14px rounded
    input shell and 200px height cap from the same audited commit.
  - Autosizing, submission and IME remain assistant-ui primitives; this app uses
    a nine-row native cap plus a 200px / 30dvh CSS cap for short windows.
  - Kept host question context, scoped drafts and the existing model-only stop;
    Pi attachments, steer/follow-up queues and session controls were not imported.
- `components/DirectoryPicker.tsx` -> `src/DirectoryPicker.tsx`
  - Adapted drive/parent navigation, typed-path confirmation and selection flow.
  - Uses generated API types, the existing portal dialog, Chinese labels and CSS
    tokens; guards stale responses and displays host eligibility reasons.
- `app/api/cwd/browse/route.ts`, `lib/directory-browser.ts` ->
  `src/abaqus_sim_agent/web/directories.py`
  - Adapted the Windows drive enumeration and immediate-child listing approach
    to Python/FastAPI. Adds bounded scans and the existing host path policy;
    workspace selection is separate from directory browsing.
- `components/SessionSidebar.tsx` -> deletion interaction in `src/ConversationList.tsx`
  and `src/App.tsx`
  - Adapted the per-conversation delete/confirm interaction using our dialog and
    host DELETE receipt. Pi's runtime shutdown and session-file deletion code
    were not copied; the Python host owns execution cleanup.

Assets:

- `public/icons/catppuccin/**` was copied into `public/icons/catppuccin/**`.
- The Catppuccin asset license is preserved at `public/icons/catppuccin/LICENSE`.

The product does not import or read the local `references/pi-web` checkout at runtime.
