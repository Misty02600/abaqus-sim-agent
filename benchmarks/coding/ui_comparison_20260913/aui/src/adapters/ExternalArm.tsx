import { useRef, useState } from "react";
import { AssistantRuntimeProvider, useExternalStoreRuntime } from "@assistant-ui/react";
import { request, runPath } from "../api";
import { RunView, type ArmProps } from "../RunView";

export function ExternalArm({ snapshot, refresh }: ArmProps) {
  const [busy, setBusy] = useState(false);
  const guard = useRef(false);
  const runtime = useExternalStoreRuntime({
    messages: snapshot.messages.aui,
    convertMessage: message => message,
    isRunning: snapshot.status === "running",
    onNew: async () => { throw new Error("Ordinary messaging is unavailable. Use the task controls."); },
  });
  async function command(action: "start" | "answer", answer?: string) {
    if (guard.current) return;
    if (action === "start" && snapshot.status !== "registered") throw new Error("This task cannot be started.");
    if (action === "answer" && (snapshot.status !== "waiting_for_user" || !snapshot.question?.pending)) throw new Error("There is no pending question.");
    guard.current = true; setBusy(true);
    try {
      await request(`${runPath(snapshot.run_id)}/${action}`, {
        command_id: crypto.randomUUID(),
        ...(action === "answer" ? { question_id: snapshot.question!.question_id, task_revision: snapshot.task_revision, answer } : {}),
      });
    } finally { await refresh(); guard.current = false; setBusy(false); }
  }
  return <AssistantRuntimeProvider runtime={runtime}><RunView snapshot={snapshot} actions={{
    start: () => command("start"), answer: answer => command("answer", answer),
    busy, answerReady: !!snapshot.question?.pending,
  }}/></AssistantRuntimeProvider>;
}
