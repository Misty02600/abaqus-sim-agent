# Official ai-chat-ui comparison fork

Built and ready for the parent's browser comparison at `http://127.0.0.1:38800/official/`.

## Baseline and license

- Upstream: https://github.com/pydantic/ai-chat-ui, version **2.1.0**.
- Baseline commit: **b9c6ae6d5a21b6a747099ae38934b99392317e2d**. The existing probe's `tag.json` binds v2.1.0 to this commit.
- Copied from `var/official-ui-probe-20260913/ai-chat-ui-2.1.0` into `var/ui-comparison-20260913/official`. The source snapshot was only read.
- The supplied snapshot contains no LICENSE, and the LICENSE URL at the baseline commit returns 404. The upstream MIT LICENSE was therefore added verbatim from the separate pinned upstream commit **f6f200816a1414e89f4cba29bd0f45c1c4856931**, not represented as a file present in v2.1.0. Exact source: https://raw.githubusercontent.com/pydantic/ai-chat-ui/f6f200816a1414e89f4cba29bd0f45c1c4856931/LICENSE. See `official/LICENSE` and `official/license-source.json`.
- `official/source-manifest.json` records every copied baseline file's source/fork SHA-256, the exact seven modified upstream files, and the two new application files. No baseline files were deleted.

## Exact modified upstream files

All paths in this table are relative to the authorized `official/` fork.

| File | Change |
| --- | --- |
| `src/App.tsx` | Removes local conversation migration and wires the host task hook, sidebar, and host panel. Reads the run ID from `/official/{id}`. Preserves the upstream theme, sidebar provider, centered main layout, query provider, and toaster. |
| `src/Chat.tsx` | Substantially reduces connection/controller code: removes useChat transport, configure endpoint, model/tool/effort selection, sending, retry/continue/edit/fork handling, composer, and IndexedDB message persistence. Accepts authoritative `UIMessage[]` props. Preserves upstream Conversation, Sources, Part, and tool grouping renderers. All tools remain visible regardless of old local filter preferences. No model loop remains. |
| `src/Part.tsx` | Adds an optional readOnly prop. This fork uses it to suppress retry, edit, fork navigation and editing UI, and to propagate read-only tool rendering. Upstream text/Markdown renderer, message layout, reasoning display, and copy action remain. |
| `src/components/app-sidebar.tsx` | Replaces local conversations with server task links, goals, IDs, and statuses. Removes deletion and its dialog. Keeps the official logo, sidebar structure/collapse controls, and theme toggle. Navigation uses normal links and reloads canonical host data. |
| `src/components/tool-part.tsx` | Adds an optional readOnly prop to hide tool filtering and approval mutation controls. Keeps official tool header, lifecycle badge, input/output components, expansion, errors, and formatting. |
| `src/components/ai-elements/tool.tsx` | Gives only ask_user a question-specific badge: input-available is Waiting for answer and output-available is Answered. Other tools and error/denial states retain their upstream labels. Chat leaves ask_user cards individually visible rather than placing consecutive questions inside a generic running tool group. |
| `vite.config.ts` | Sets normal build base to `/official/`, development proxy default to port 38800, and reuses the upstream icon-inlining plugin for normal builds. Expands its URL matching to root/base-relative icons. All four existing icons are embedded to work with the host's assets-only mount. Offline build configuration remains available but was not built. |

The upstream `src/index.css`, theme components, `Response`/Streamdown renderer, AI element components other than the explicit tool badge change, package.json, pnpm-lock.yaml, bun.lock, and all copied tests are unchanged. Inactive upstream helpers/components remain in the fork; their existence does not imply supported product actions.

## New application code

- `src/host-api.ts`: typed snapshot/list declarations, same-origin GET/POST JSON helper, and a React hook. Loads the list and selected snapshot initially; listens to native EventSource `sync` and `run.changed`; reloads on reconnect and window focus. Aborts superseded reads and ignores stale responses. Checks run ID/arm binding and presence of Vercel history. Read errors and reconnect state are visible. It does not write client history back or infer verification.
- `src/components/host-panel.tsx`: accessible Goal/Create task, Start task, and Answer/Submit answer controls; explicit host status, phase, revision, question ID/text/recorded answer, outcome, scope, verification feedback and usage. Starts only registered tasks and submits only a pending question while waiting_for_user. Uses UUID command IDs for start/answer, guards in-flight double submission, and retains the same ID for an unchanged command following a failed POST within the mounted page.

Other new files are the license/provenance records, this note, `source-manifest.json`, `build-checks.json`, install/build/lint logs, and generated local tooling/dependency/build artifacts. No product source, product tests, Python dependencies, ADRs, plans, backend, or source snapshot were edited.

## API and browser handoff

- Only the agreed `/api/runs?arm=official`, `/api/runs`, `/api/runs/{id}`, `/start`, `/answer`, and `/events` endpoints are used. The goal is posted with `arm: official`; start posts only command_id; answer posts command_id, question_id, task_revision and answer.
- Create waits for the host's run ID, then navigates to `/official/{id}`. Opening a link, reloading, receiving SSE, or focusing the page only reads data; none starts a task or answers a question.
- `snapshot.messages.vercel` is rendered without client projection/reconciliation. The parent confirmed that answered ask_user tools arrive with output. The question badge maps input-available to Waiting for answer and output-available to Answered; other tools still use Running/Completed. No card state is inferred from model text or overall outcome.
- `data-testid="host-status"` contains raw snapshot.status.
- `data-testid="host-scope"` exists when outcome exists and contains outcome.verification_scope.
- `data-testid="host-verdict"` exists when verification exists and contains literal `pass` or `fail`, derived exclusively from verification.passed. Before a host result exists the panel says Not reported.
- `Goal` and `Answer` are explicit labels attached to their textareas. Action labels are exactly Create task, Start task, Submit answer. No Retry/Edit/Fork/Delete/Cancel/effort or approval mutation controls are mounted in the comparison.
- The conversation is intentionally snapshot based, with no token streaming. SSE transport changes are displayed separately from task status. There is no second model loop.
- The parent's SLOW and FAIL fixtures were not invoked here. Actual ask_user/write_file behavior, disconnect recovery, missing-deliverable failure, and the three fixtures per arm remain the parent's browser verification responsibility.

## Build and checks

- Node **24.12.0**, locally installed pnpm **10.29.3**, matching the upstream packageManager version.
- The pre-existing pnpm shim reported 11.19.0 and rejected frozen-lockfile; that failed attempt was replaced with the pinned local pnpm executable. No global installation was performed.
- Install passed with **--frozen-lockfile --ignore-scripts**, with store/cache under `official/`. 1,186 packages were installed; package.json and both upstream lockfiles remain byte-identical. The separate `.tooling/` package only supplies pnpm. Husky/lifecycle scripts were not run.
- Final **tsc -b && vite build passed**, including the final raw-history binding, test IDs, and icon changes. The latest question-badge follow-up build completed in 12.94 seconds. A first build caught an optional-input type error in a temporary question projection; that projection was subsequently removed entirely after backend confirmation. It is absent from the final code.
- Targeted ESLint passed with zero findings on all seven changed upstream files and both new application files. Earlier lint findings were corrected before the final build.
- Output: `official/dist/index.html` and `official/dist/assets/`; 414 dist files, 15,658,906 bytes total. Entry JS is 1,789.19 kB before gzip. Vite reports its standard >500 kB chunk warning; upstream syntax-highlighting/diagram capabilities were retained rather than redesigned for this isolated comparison.
- Read-only HTTP checks against the shared host passed: `/official/` returned HTML 200; both built entry JS/CSS URLs returned 200 with the expected JavaScript/CSS content types. Initial official task list GET returned an empty runs array.
- Hosting mismatch found and handled in the fork: `/official/favicon.svg` returned HTML 200 from the catch-all task route. The final build embeds the four existing upstream icons. HTML has zero remote script/stylesheet URLs; Vite's runtime chunks/styles/fonts are built locally with `/official/` base. No CDN runtime configuration remains.
- `official/build-checks.json` records output hashes/counts and HTTP evidence; `install.log`, `tooling-install.log`, `build.log`, and `lint.log` retain command outputs.
- No browser automation, new fixture runs, model calls, Abaqus calls, visible shells, or independently started backend were used. No claim is made that browser fixtures have passed.

Rebuild from the authorized fork directory:

```powershell
node .tooling/node_modules/pnpm/bin/pnpm.cjs run build
```

For a fresh dependency installation in that directory, use the local pinned pnpm with `install --frozen-lockfile --ignore-scripts --store-dir .pnpm-store --cache-dir .pnpm-cache`.

## Limits

Parent-reported browser development follow-up: after the shared server MIME correction and a fresh browser, create/start reached waiting_for_user on `cmp-e8d6b911ba37495c`. The host panel was correct but upstream's generic tool badge showed Running. A read-only snapshot GET confirmed `tool-ask_user` with `state: input-available`. The fork now gives that state the visible label Waiting for answer, and output-available the label Answered. Only ask_user is excluded from generic consecutive-tool grouping so an aggregate running label cannot reintroduce the ambiguity. This follow-up received one build; no browser automation or task mutation was performed here. The development sample was left waiting for the parent, whose qualification matrix is still pending.

- The shared host owns persistence, command deduplication, lifecycle semantics and completed tool output. This client has TypeScript declarations plus small binding checks, not a full runtime JSON schema validator.
- Commands are explicit; failed/ambiguous requests show an error and refresh the host snapshot. A Create request has no idempotency key in the agreed API, so an ambiguous create response cannot guarantee deduplication if a user resubmits. Start/answer IDs survive an in-page failed submission, not a complete page reload. No automatic POST retry is performed.
- Task drafts are transient. Task/history navigation reloads the page. The index task list refreshes on load/focus; only a selected task has SSE. Connection loss retains the last snapshot with a stale/reconnecting notice until refresh succeeds.
- Verification scope/result are displayed exactly as host evidence; model claims and tool completion are never promoted to task or physical verification. This UI work supplies no new physical or product capability evidence.
