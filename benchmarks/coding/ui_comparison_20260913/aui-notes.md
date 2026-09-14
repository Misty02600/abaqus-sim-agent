# Isolated assistant-ui comparison — 2026-09-13

Both modes are implemented in one React/TypeScript/Vite application. This worker wrote only `var/ui-comparison-20260913/aui/` and this file. No product source, tests, docs, Python lockfile, model, or Abaqus invocation was used by this worker. Headless JavaScript checks use mocked HTTP; browser and shared-backend validation belong to the parent.

## Serve and build

- Entry routes: `/external`, `/external/{run_id}`, `/agui`, `/agui/{run_id}`.
- Parent serves `aui/dist/index.html` for both route families and mounts `aui/dist` at `/aui-assets`.
- Vite base: `/aui-assets/`. Generated references are `/aui-assets/assets/index-Bbw5I3H5.js` and `/aui-assets/assets/index-7wDN5taj.css`.
- In `aui/`: `npm ci`, `npm run typecheck`, `npm test`, `npm run build`. The included `.npmrc` puts npm cache in the same isolated directory.
- Pinned production dependencies: `@assistant-ui/react` 0.15.19, `@assistant-ui/react-ag-ui` 0.0.59, `@ag-ui/client` 0.0.59, React/React DOM 19.2.3. Vite 7.3.1, TypeScript 5.8.3. npm lock committed to the isolated artifact, SHA-256 `de96b7fdd4c121379c812e6b305422e68445610372fb6aca8981d0cd43d73a46`.
- `npm ls` confirms one shared `@assistant-ui/core` 0.3.18 and `@assistant-ui/store` 0.3.13, avoiding split runtime contexts.

## Shared versus adapter-specific code

Count is source TS/TSX modules, excluding dependencies, build configuration, generated output, CSS, and checks. There are **8 application modules: 5 shared and 3 adapter-specific**.

| Classification | Module | Responsibility |
| --- | --- | --- |
| Shared | `src/main.tsx` | React mount |
| Shared | `src/App.tsx` | Route selection, mode links, task list, Goal/Create task form |
| Shared | `src/api.ts` | Shared host types, read/create request helper, paths |
| Shared | `src/useHost.ts` | GET bootstrap, EventSource refresh, read-only polling, stale-response suppression |
| Shared | `src/RunView.tsx` | Identical conversation, Start/Answer controls, render-only tool card, host outcome panel |
| External only | `src/adapters/ExternalArm.tsx` | `useExternalStoreRuntime`, identity `ThreadMessageLike` converter, REST start/answer with UUID |
| AG-UI only | `src/adapters/AgUiArm.tsx` | Genuine `HttpAgent`, `useAgUiRuntime`, native append/interrupt submission, client completion subscription |
| AG-UI only | `src/adapters/aguiHistory.ts` | Host snapshot → native history repository and pending-interrupt metadata |

The shared stylesheet is `src/styles.css`. `checks/runtime.test.tsx` is one isolated headless check module. Before final documentation, shared TS/TSX totaled 164 lines; External adapter 31; AG-UI adapter plus history totaled 96. Formatting is compact, so module identity and responsibility are more informative than line count.

Both arms render `ThreadPrimitive.Root`, `ThreadPrimitive.Viewport`, `ThreadPrimitive.Messages`, `MessagePrimitive.Root`, and `MessagePrimitive.Parts`. The same fallback tool renderer displays tool name, runtime status, arguments, and result. For ask_user it shows Waiting for answer while result is undefined and Answered when result is present; both semantics have headless assertions. It registers no tool definition or executor. No Retry/Edit/Fork/Delete/Cancel, ordinary composer, model selection, or effort controls are mounted. Completed/failed tasks have no sending action.

Accessible labels: Goal, Create task, Start task, Answer, Submit answer. Host panel selectors: `host-status` (raw status), `host-scope` (outcome scope or Not available), `host-verdict` (Passed/Failed from verification or Not available). Host verdict and scope remain separate from assistant text.

## Exact AG-UI adaptation

No backend endpoint or API change is needed.

1. Bootstrap uses the same GET snapshot as External. Construct `HttpAgent` with `/api/agui/{run_id}` and `threadId: snapshot.run_id`; explicitly call `agent.setMessages(snapshot.messages.agui)`.
2. A public `ThreadHistoryAdapter.load()` returns `ExportedMessageRepository.fromArray(fromAgUiMessages(snapshot.messages.agui))` after adding the pending interrupt information described below. `append()` is intentionally a no-op because only the host owns persistence. There is no `unstable_resume`, `resume()` network replay, or automatic start during hydration.
3. In the pinned 0.0.59 source, `getPendingInterrupts()` reads the latest assistant message only when `status` is `{type: "requires-action", reason: "interrupt"}`, then reads `metadata.custom.agui.interrupts`. The adapter populates exactly these fields from the host's pending question, preserving the question ID, toolCallId, reason, message, and specified answer/revision response schema. If no assistant message exists, it creates a local display anchor containing the host question. This is a version-bound message-metadata mapping through a supported history API, not a backend event or persisted fabricated assistant turn.
4. Host refreshes import the same repository through public `runtime.thread.import()` only when native runtime/dispatch is idle. `agent.setMessages()` is kept aligned. Incoming active-run events remain handled by the genuine runtime. `STATE_SNAPSHOT` is consumed natively; the separate host result panel continues to use GET equally in both arms.
5. Start uses native `runtime.thread.append("Start task")`. Answer **only** calls `useAgUiSubmitInterruptResponses()` with `[{interruptId: qid, status: "resolved", payload: {answer, task_revision: 0}}]`. There is no AG-UI REST `/answer` fallback.
6. Client `onRunFinalized` releases dispatch bookkeeping and triggers a GET after a deferred JavaScript task. This fixes a reproduced fast-response case where React batched away the intermediate `isRunning: true` render. Waiting for a rendered falling edge alone left the local dispatch guard locked even after genuine RUN_FINISHED.

Actual outgoing HttpAgent request assertions confirmed `threadId` equals the host run ID and `tools` is `[]` for both start and resume. Native resume responses preserve the supplied payload. No client tool implementation is registered.

## Verification and observed limitations

`npm run typecheck` passes. `npm run build` passes. **9 headless checks pass**, exercising the actual installed hooks, primitives, history adapter, HttpAgent, and SSE parser with mocked HTTP:

- Pending-page reload hydrates a native interrupt without any HTTP write; native submission POSTs the exact resume payload and zero tools.
- Registered-page start sends native Start task; genuine AG-UI interrupt events finish; a subsequent host snapshot exposes an answerable native interrupt.
- External answer POST contains a UUID, question ID, revision, and answer, and uses only REST.
- Both arms render host failure verdict/scope and expose no sending controls on a closed task.
- History supports a pending host question with no existing assistant message.
- Both arms' host-sync error/open/sync/run.changed transitions issue only GETs and close EventSource on unmount.

The tests do not establish browser layout quality, shared-backend end-to-end success, real process-disconnect recovery, physical correctness, or model behavior. Those results must be recorded separately by the parent; this worker did not open a browser or invoke the shared backend's commands.

Remaining tradeoffs:

- The AG-UI history seam is supported, but its `custom.agui.interrupts` convention is tied to inspected 0.0.59 internals and needs rechecking on upgrade. This is the main additional adaptation versus External's direct snapshot mapping.
- Both arms have a shared read-only four-second GET fallback and SSE-triggered GET. Reconnect never starts or resumes a task. This restores host state rather than reattaching an interrupted AG-UI POST stream. A tab reopened during an ongoing task waits for host changes.
- Task switches are ordinary full-page navigations. There is no cross-tab coordinator or durable browser command queue. Duplicate-command and execution authority remain with the backend.
- HTTP/type contracts are TypeScript types, not a new runtime validation layer; the host remains authoritative. Transport errors are surfaced, and the host's last known state remains visible while reads reconnect.
- The one shared bundle includes both runtimes: approximately 753.51 kB JS / 210.29 kB gzip and 5.43 kB CSS / 1.83 kB gzip. Vite reports the expected >500 kB warning and ignored dependency `use client` directives. No bundle-size claim or startup-performance comparison was made.
- Plain text and render-only tool details are sufficient for this comparison; there is no Markdown code highlighting, artifact download UI, attachment UI, optimistic message persistence, or general chat composer.
