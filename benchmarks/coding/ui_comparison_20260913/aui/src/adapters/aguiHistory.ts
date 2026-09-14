import { ExportedMessageRepository, type ThreadHistoryAdapter } from "@assistant-ui/react";
import { fromAgUiMessages, type AgUiInterrupt } from "@assistant-ui/react-ag-ui";
import type { Snapshot } from "../api";

export function hostRepository(snapshot: Snapshot) {
  const messages = fromAgUiMessages(snapshot.messages.agui);
  const question = snapshot.status === "waiting_for_user" && snapshot.question?.pending ? snapshot.question : null;
  if (question) {
    const interrupt: AgUiInterrupt = {
      id: question.question_id, reason: "input_required", toolCallId: question.question_id,
      message: question.question,
      responseSchema: { type: "object", properties: { answer: { type: "string" }, task_revision: { type: "integer" } }, required: ["answer", "task_revision"] },
    };
    let index = messages.findLastIndex(message => message.role === "assistant");
    if (index < 0) {
      index = messages.length;
      messages.push({ id: `host-question-${question.question_id}`, role: "assistant", content: [{ type: "text", text: question.question }] });
    }
    const message = messages[index]!;
    // Native 0.0.59 history contract: latest assistant requires-action + custom.agui.interrupts.
    messages[index] = { ...message, status: { type: "requires-action", reason: "interrupt" }, metadata: { ...message.metadata, custom: { ...message.metadata?.custom, agui: { interrupts: [interrupt] } } } };
  }
  return ExportedMessageRepository.fromArray(messages);
}

export function hostHistory(snapshot: Snapshot): ThreadHistoryAdapter {
  return {
    load: async () => hostRepository(snapshot),
    // Host owns all persistence. Never write browser history or auto-resume on load.
    append: async () => {},
  };
}
