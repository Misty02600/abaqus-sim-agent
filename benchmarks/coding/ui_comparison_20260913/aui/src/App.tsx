import { useRef, useState } from "react";
import { errorText, request, type Arm, type Snapshot } from "./api";
import { useHost } from "./useHost";
import { ExternalArm } from "./adapters/ExternalArm";
import { AgUiArm } from "./adapters/AgUiArm";

export function App() {
  const [, route, encodedId] = window.location.pathname.split("/");
  const arm: Arm = route === "agui" ? "agui" : "external";
  const runId = encodedId ? decodeURIComponent(encodedId) : undefined;
  const host = useHost(arm, runId);
  const [goal, setGoal] = useState("");
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState("");
  const guard = useRef(false);
  async function create() {
    if (guard.current || !goal.trim()) return;
    guard.current = true; setCreating(true); setError("");
    try {
      const snapshot = await request<Snapshot>("/api/runs", { arm, goal: goal.trim() });
      window.location.assign(`/${arm}/${encodeURIComponent(snapshot.run_id)}`);
    } catch (error) { setError(errorText(error)); guard.current = false; setCreating(false); }
  }
  return <div className="app"><header className="topbar"><div className="brand"><span className="brand-mark">A</span><div><strong>Abaqus workspace</strong><div className="muted small">Isolated UI comparison</div></div></div><nav aria-label="Comparison modes"><a href="/external" aria-current={arm === "external" ? "page" : undefined}>External store</a><a href="/agui" aria-current={arm === "agui" ? "page" : undefined}>AG-UI runtime</a></nav></header>
    <div className="workspace"><aside className="sidebar"><div className="eyebrow">{arm === "external" ? "EXTERNAL STORE" : "AG-UI"}</div><h1>Tasks</h1><form onSubmit={event => { event.preventDefault(); void create(); }}><label htmlFor="goal">Goal</label><textarea id="goal" name="goal" rows={4} placeholder="Describe the modeling task…" value={goal} onChange={event => setGoal(event.target.value)} disabled={creating}/><button disabled={creating || !goal.trim()}>Create task</button></form>
      <div className="task-list" aria-label="Task list">{host.runs.length === 0 && <p className="muted small">No tasks in this mode yet.</p>}{host.runs.map(run => <a key={run.run_id} href={`/${arm}/${encodeURIComponent(run.run_id)}`} aria-current={runId === run.run_id ? "page" : undefined}><strong>{run.goal}</strong><span>{run.status.replaceAll("_", " ")}</span></a>)}</div>
    </aside><main><div className="connection muted small">{runId ? host.connected ? "Live host updates" : "Host updates reconnecting · read-only refresh active" : "Create or select a task"}</div>{(host.error || error) && <p className="error" role="alert">{host.error || error}</p>}
      {host.snapshot ? arm === "external" ? <ExternalArm key={host.snapshot.run_id} snapshot={host.snapshot} refresh={host.refresh}/> : <AgUiArm key={host.snapshot.run_id} snapshot={host.snapshot} refresh={host.refresh}/> : <section className="welcome"><div className="eyebrow">ONE TASK · HOST-OWNED EXECUTION</div><h2>Start with a goal.</h2><p>Create a task, review the conversation, and answer questions when the host needs your input.</p></section>}
    </main></div></div>;
}
