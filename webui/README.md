# Frontend source and distribution

This directory contains the local workbench UI, using React 19.2.3,
`@assistant-ui/react` 0.15.19 and `@assistant-ui/react-markdown` 0.14.15.
`package-lock.json` binds the full dependency tree. Vite builds static resources;
there is no frontend server in the installed Python application.

Run `uv sync --locked --all-groups --extra web` and
`uv run --no-sync python scripts/build_web.py` from the repository root.
This generates `openapi.json` and `src/api/schema.ts`, runs `npm ci`, checks
TypeScript, builds assets and publishes the index last into the Python package.
Do not edit the generated schema. CI rejects a stale generated API.

For development, start the Python Web command on port 8765 with `--no-open`, run
`npm run dev`, and open the plain dev origin. The Vite proxy preserves the origin;
the first session GET establishes the local browser cookie and CSRF automatically.

- `App.tsx` owns page selection, first-message send, follow-up messages, answers,
  explicit continuation, the Pi-inspired workbench layout and global shortcuts.
- `DirectoryPicker.tsx` adapts Pi's drive/directory navigation to the authenticated
  `/directories` API. New runs can bind an existing workspace or use a managed
  empty folder. The selection is an unsent session-scoped preference; the host
  revalidates and persists the canonical binding on creation. No workspace is
  created or model run started by browsing folders.
- `WorkspaceExplorer.tsx` refreshes expanded folders when the host file manifest
  changes and discards superseded directory responses, so new files appear after
  a run settles without a manual refresh.
- `Composer.tsx` wraps native Composer Root/Input/Send primitives; keyboard, IME, resizing and send availability come from the library. It only hydrates/persists the scoped draft and renders question context.
  The Pi-inspired input starts at one line with the send action beside it, grows
  with the draft, then scrolls internally at 200px (or 30dvh in short windows).
  Clearing or sending the draft restores the single-line height. Disabled-action
  reasons remain visible; keyboard hints are available on the input/send action.
- `WorkbenchRuntime.tsx` owns the shared ExternalStore adapter for messages, native composer submission and controlled thread selection. It translates the host delivery receipt into native draft recovery.
- `Conversation.tsx` projects/renders host messages with native message primitives;
  tool calls use host-projected summaries and keep full payloads in disclosure panels.
- `ConversationList.tsx` uses native ThreadList and ThreadListItem primitives for listing, selection, titles and keyboard navigation.
- `useRun.ts` fetches snapshots after SSE notifications; stale responses cannot
  overwrite another selected task. It never starts model work.
- `Artifacts.tsx`, `SidePanel.tsx` and `TurnWrittenFiles.tsx` expose current-revision
  files on demand, with text and PNG previews. `Conversation.tsx` and
  `ModelPreview.tsx` additionally render fresh OBJ artifacts from the current CAE
  tool card through Online 3D Viewer. They never infer freshness locally or read
  CAE/ODB directly.
- `RunStatusBar.tsx`, `RunSummary.tsx` and `RunDetails.tsx` render host phase,
  connection, usage, verification and capability facts. Disabled operations remain
  visible with the state-based reason. A running Solver uses the host ToolExecution
  snapshot for status, elapsed time, PID/details and current-process cancellation.
- `RuntimePolicyEditor.tsx` updates the host-owned Abaqus/Solver permissions by
  operational amendment. It shows cumulative usage and total limits; saving does
  not run the model, reset usage, or cancel an existing execution. The host keeps
  pending questions and rejects edits while execution is active or unresolved.
- `CommandPalette.tsx` provides session search and local shortcuts. It dispatches
  the same state-checked handlers as the visible controls and owns no run state.
- `api/client.ts` handles session/CSRF and retry IDs; generated types are the API
  contract. Browser session storage contains retry IDs and unsent drafts, not authoritative task history or outcomes.

The desktop sidebar participates in the page grid at widths of 900px and above;
collapsing it leaves a single content column. Below 900px it is an overlay.
SidePanel dialogs render through a portal outside the sidebar so resizing cannot
hide an open modal and leave the page blocked. Start errors, including errors in
accepted HTTP receipts, remain visible across snapshot refreshes until the user
retries or navigates away; retry uses the already saved conversation.

For manual browser previews and native window-resize checks, launch Playwright
with `contextOptions.viewport: null` and resize the actual browser window. CLI
`resize` / `page.setViewportSize()` enables a fixed emulated viewport: dragging
the outer window afterward does not make the page follow its new size. Reopen a
non-emulated preview before handing it to a user. Viewport-size layout checks
alone do not verify native window resizing.

No official Pydantic UI source or assets were copied. The visual layer also
adapts selected presentational code and assets from Pi Web: file/thinking icons,
Catppuccin file assets and compact interaction patterns. Exact source commits,
file mappings, changes and bundled MIT notices are recorded in
[`THIRD_PARTY_REUSE.md`](THIRD_PARTY_REUSE.md). Pi session/runtime semantics were
not imported; assistant-ui and CodingRun continue to own their existing layers.
Runtime libraries are npm
dependencies; their notices are collected during build and shipped as
`web_static/THIRD_PARTY_NOTICES.txt`. An upstream license missing from the
react-remove-scroll-bar tarball is preserved under `licenses/` with commit and
hash provenance. use-composed-ref declares MIT but distributes no LICENSE text;
its package declaration and repository are retained in generated notices.

The September 14 npm audit reported one low-severity issue in esbuild's own
Windows development server. This application uses Vite's build/transform path
and does not call esbuild.serve; its deployed Python server does not run Node.
The advisory is recorded rather than applying a forced major-version override.

Reply actions now use assistant-ui's Copy primitive. Conversation titles are
versioned display metadata under the registry; changing a title does not amend
the user request. The Stop action carries the current active-operation identity
so a stale click cannot cancel a later work segment. It cancels the current Agent
run and its foreground tools, waits for their cleanup, and preserves completed
work plus cumulative usage. A submitted background Solver keeps running and has
its own cancellation action. A new Agent segment stays blocked until that Solver
reaches a terminal state and the workspace exactly matches its recorded terminal
snapshot. Stop ends the current segment; it is not pause/resume.

Conversation deletion is a separate confirmed operation: the host cancels and
drains its owned execution before permanently removing state/registry records.
Project files remain in place. Only a successful DELETE receipt removes the
conversation in the initiating UI; failed cleanup keeps a retry action. Other
tabs leave the deleted conversation after a refreshed snapshot returns 410.

Web model requests use Pydantic AI's native node stream. Public text and typed
final-output summaries update an in-memory snapshot (coalesced at 50 ms); SSE
still invalidates snapshots rather than replaying append-only token offsets.
Thinking parts and ordinary tool arguments are not exposed as streamed prose.
The live preview is replaced by persisted history at checkpoints. On user stop,
partial public text is kept as an explicitly incomplete reply, not an outcome;
stopping during a foreground tool records the same interrupted boundary without
inventing an assistant message.

The controlled `adapters.threadList` integration in pinned assistant-ui 0.15.19
is marked experimental/deprecated upstream. It is isolated in WorkbenchRuntime
and must be re-checked on library upgrades. This does not change backend thread
identity or require creating empty host runs when the user clicks New.

The native composer clears text when dispatch begins. Its MessageNotSentError
restores the draft if delivery is not confirmed; the host remains authoritative.
An exact message ID from a later snapshot can resolve a lost HTTP acknowledgement
and clear the restored draft. Never infer delivery merely from matching text.
No client-side tool execution, attachment adapter or queue is enabled here.
