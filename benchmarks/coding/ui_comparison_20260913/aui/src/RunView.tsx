import { useState } from "react";
import { MessagePrimitive, ThreadPrimitive, type ToolCallMessagePartProps } from "@assistant-ui/react";
import { errorText, type Snapshot } from "./api";

export type ArmProps = { snapshot: Snapshot; refresh: () => Promise<void> };
export type Actions = { start: () => void | Promise<void>; answer: (answer: string) => Promise<void>; busy: boolean; answerReady: boolean; error?: string };
const pretty = (value: unknown) => typeof value === "string" ? value : JSON.stringify(value, null, 2);

function ToolResult({ toolName, args, result, status }: ToolCallMessagePartProps) {
  const label = toolName === "ask_user" ? result === undefined ? "Waiting for answer" : "Answered" : status.type;
  return <details className="tool-card"><summary><span>Tool · {toolName}</span><span className="muted">{label}</span></summary>
    <div className="tool-body"><strong>Arguments</strong><pre>{pretty(args)}</pre><strong>Result</strong><pre>{result === undefined ? "Awaiting host result" : pretty(result)}</pre></div>
  </details>;
}
function UserMessage() {
  return <MessagePrimitive.Root className="message user-message"><div className="message-label">User</div><MessagePrimitive.Parts /></MessagePrimitive.Root>;
}
function AssistantMessage() {
  return <MessagePrimitive.Root className="message assistant-message"><div className="message-label">Assistant</div><MessagePrimitive.Parts components={{ tools: { Fallback: ToolResult } }} /></MessagePrimitive.Root>;
}

export function HostResult({ snapshot }: { snapshot: Snapshot }) {
  return <aside className="host-panel" aria-label="Host state"><div className="eyebrow">HOST STATE</div><h2>Execution evidence</h2>
    <dl><dt>Status</dt><dd data-testid="host-status">{snapshot.status}</dd><dt>Phase</dt><dd>{snapshot.phase || "—"}</dd><dt>Revision</dt><dd>{snapshot.task_revision}</dd>
      <dt>Verdict</dt><dd data-testid="host-verdict">{snapshot.verification ? snapshot.verification.passed ? "Passed" : "Failed" : "Not available"}</dd>
      <dt>Scope</dt><dd data-testid="host-scope">{snapshot.outcome?.verification_scope ?? "Not available"}</dd></dl>
    {snapshot.outcome && <><h3>Host outcome · {snapshot.outcome.status}</h3><pre>{pretty(snapshot.outcome.summary)}</pre><h3>Usage</h3><pre>{pretty(snapshot.outcome.usage)}</pre></>}
    {snapshot.verification && <><h3>Verification feedback</h3><pre>{pretty(snapshot.verification.feedback)}</pre></>}
    <p className="muted small">Verdict and scope come from the host. Assistant text does not change them.</p>
    <div className="run-id">{snapshot.run_id}</div>
  </aside>;
}

export function RunView({ snapshot, actions }: { snapshot: Snapshot; actions: Actions }) {
  const [answer, setAnswer] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const pending = snapshot.status === "waiting_for_user" && snapshot.question?.pending;
  const disabled = actions.busy || sending;
  async function submit(kind: "start" | "answer") {
    if (disabled) return;
    setSending(true); setError("");
    try { if (kind === "start") await actions.start(); else { await actions.answer(answer.trim()); setAnswer(""); } }
    catch (error) { setError(errorText(error)); }
    finally { setSending(false); }
  }
  return <div className="run-layout"><section className="conversation" aria-label="Conversation">
    <div className="conversation-heading"><div><div className="eyebrow">TASK CONVERSATION</div><h2>{snapshot.goal}</h2></div><span className={`status status-${snapshot.status}`}>{snapshot.status.replaceAll("_", " ")}</span></div>
    <ThreadPrimitive.Root className="thread"><ThreadPrimitive.Viewport className="thread-viewport" autoScroll={false}>
      <ThreadPrimitive.Empty><p className="empty">No messages yet. Start the registered task when you are ready.</p></ThreadPrimitive.Empty>
      <ThreadPrimitive.Messages components={{ UserMessage, AssistantMessage }} />
    </ThreadPrimitive.Viewport></ThreadPrimitive.Root>
    <div className="task-actions">
      {snapshot.status === "registered" && <button disabled={disabled} onClick={() => void submit("start")}>Start task</button>}
      {pending && <form onSubmit={event => { event.preventDefault(); void submit("answer"); }}><div className="eyebrow">INPUT REQUIRED</div><p className="question">{snapshot.question!.question}</p><label htmlFor="answer">Answer</label><div className="answer-row"><input id="answer" name="answer" value={answer} onChange={event => setAnswer(event.target.value)} disabled={disabled} autoComplete="off" /><button disabled={disabled || !actions.answerReady || !answer.trim()}>Submit answer</button></div>
        {!actions.answerReady && <p className="muted small">Loading the pending question into the conversation runtime…</p>}</form>}
      {snapshot.status === "running" && <p className="muted">The host is running this task.</p>}
      {(snapshot.status === "completed" || snapshot.status === "failed") && <p className="muted">Task closed · see the host outcome.</p>}
      {(error || actions.error) && <p role="alert" className="error">{error || actions.error}</p>}
    </div>
  </section><HostResult snapshot={snapshot}/></div>;
}
