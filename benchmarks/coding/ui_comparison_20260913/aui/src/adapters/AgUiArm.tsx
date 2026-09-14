import { useEffect, useRef, useState } from "react";
import { HttpAgent } from "@ag-ui/client";
import { AssistantRuntimeProvider, useAuiState, type AssistantRuntime } from "@assistant-ui/react";
import { useAgUiRuntime, useAgUiInterrupts, useAgUiSubmitInterruptResponses } from "@assistant-ui/react-ag-ui";
import { RunView, type ArmProps } from "../RunView";
import { hostHistory, hostRepository } from "./aguiHistory";

export function AgUiArm(props: ArmProps) {
  const [error, setError] = useState("");
  const [agent] = useState(() => {
    const next = new HttpAgent({ url: `/api/agui/${encodeURIComponent(props.snapshot.run_id)}`, threadId: props.snapshot.run_id });
    next.setMessages(props.snapshot.messages.agui);
    return next;
  });
  const [history] = useState(() => hostHistory(props.snapshot));
  const runtime = useAgUiRuntime({ agent, adapters: { history }, onError: error => setError(error.message) });
  return <AssistantRuntimeProvider runtime={runtime}><AgUiControls {...props} runtime={runtime} agent={agent} error={error} clearError={() => setError("")} /></AssistantRuntimeProvider>;
}

function AgUiControls({ snapshot, refresh, runtime, agent, error, clearError }: ArmProps & { runtime: AssistantRuntime; agent: HttpAgent; error: string; clearError: () => void }) {
  const interrupts = useAgUiInterrupts();
  const submitInterrupts = useAgUiSubmitInterruptResponses();
  const running = useAuiState(state => state.thread.isRunning);
  const loading = useAuiState(state => state.thread.isLoading);
  const guard = useRef(false);
  const [dispatching, setDispatching] = useState(false);
  const imported = useRef("");

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    const subscription = agent.subscribe({ onRunFinalized: () => {
      // Client finalization precedes runtime cleanup. Defer hydration to the next task.
      timer = setTimeout(() => { guard.current = false; setDispatching(false); void refresh(); }, 0);
    } });
    return () => { subscription.unsubscribe(); clearTimeout(timer); };
  }, [agent, refresh]);

  useEffect(() => {
    if (running || dispatching) return;
    if (!loading && !guard.current && imported.current !== snapshot.view_version) {
      agent.setMessages(snapshot.messages.agui);
      runtime.thread.import(hostRepository(snapshot));
      imported.current = snapshot.view_version;
    }
  }, [running, loading, dispatching, snapshot, agent, runtime]);

  const ready = !!snapshot.question?.pending && interrupts.some(item => item.id === snapshot.question!.question_id);
  return <RunView snapshot={snapshot} actions={{ busy: running || loading || dispatching,
    answerReady: ready, error,
    start: () => {
      if (guard.current || loading || running || snapshot.status !== "registered") return;
      guard.current = true; setDispatching(true); clearError();
      // Native assistant-ui action -> useAgUiRuntime -> HttpAgent -> RunAgentInput.
      runtime.thread.append("Start task");
    },
    answer: async answer => {
      if (guard.current || !ready || running || snapshot.status !== "waiting_for_user") throw new Error("The native interrupt is not ready for an answer.");
      guard.current = true; setDispatching(true); clearError();
      try {
        await submitInterrupts([{ interruptId: snapshot.question!.question_id, status: "resolved", payload: { answer, task_revision: snapshot.task_revision } }]);
      } finally { guard.current = false; setDispatching(false); imported.current = ""; await refresh(); }
    },
  }}/>;
}
