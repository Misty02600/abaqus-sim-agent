import {
  AssistantRuntimeProvider,
  MessageNotSentError,
  useExternalStoreRuntime,
  type ThreadMessageLike,
} from "@assistant-ui/react";
import { useEffect, useMemo, useRef, type ReactNode } from "react";
import { toMessages } from "./Conversation";
import type { Snapshot } from "./api/client";
import { runLabel } from "./runPresentation";

export type Delivery = { saved: boolean; messageId?: string };

export function WorkbenchRuntime({
  id,
  snapshot,
  runs,
  busy,
  draftKey,
  onSend,
  onNavigate,
  onRecoveredDelivery,
  children,
}: {
  id: string | null;
  snapshot: Snapshot | null;
  runs: Snapshot[];
  busy: boolean;
  draftKey: string;
  onSend: (text: string) => Promise<Delivery>;
  onNavigate: (id: string | null) => void;
  onRecoveredDelivery: () => void;
  children: ReactNode;
}) {
  const messages = useMemo(
    () => (snapshot ? toMessages(snapshot.view) : []),
    [snapshot?.view],
  );
  const threads = useMemo(
    () =>
      runs.map((run) => ({
        id: run.view.run_id,
        status: "regular" as const,
        title: run.title,
        custom: {
          label: runLabel(run.view),
          runId: run.view.run_id,
          title: run.title,
          titleVersion: run.title_version ?? 0,
          active: run.active ?? false,
          createdAt: run.created_at,
          messageCount: run.view.messages?.length ?? 0,
        },
      })),
    [runs],
  );
  const pending = useRef<{ text: string; messageId?: string } | null>(null);
  const latestMessages = useRef(messages);
  latestMessages.current = messages;
  const allowed =
    !busy &&
    (!id ||
      !!(snapshot?.view.question?.pending
        ? snapshot.can_answer
        : snapshot?.can_message));
  const storageKey = `abaqus-draft:${draftKey}`;
  const runtime = useExternalStoreRuntime<ThreadMessageLike>({
    messages,
    convertMessage: (message) => message,
    isRunning: snapshot?.active ?? false,
    isLoading: !!id && !snapshot,
    isSendDisabled: !allowed,
    // This controlled thread-list adapter is isolated here because its pinned
    // 0.15.19 API is marked experimental/deprecated by the library.
    adapters: {
      threadList: {
        threadId: id ?? "new",
        threads,
        onSwitchToNewThread: () => onNavigate(null),
        onSwitchToThread: onNavigate,
      },
    },
    onNew: async (message) => {
      const text = message.content
        .filter((part) => part.type === "text")
        .map((part) => part.text)
        .join("\n");
      if (
        !allowed ||
        !text.trim() ||
        message.attachments?.length ||
        message.content.some((part) => part.type !== "text")
      )
        throw new MessageNotSentError();
      const submission: { text: string; messageId?: string } = { text };
      pending.current = submission;
      if (!sessionStorage.getItem(storageKey))
        sessionStorage.setItem(storageKey, text);
      const result = await onSend(text);
      submission.messageId = result.messageId;
      // A late snapshot may confirm delivery after the HTTP acknowledgement was lost.
      const recovered =
        !!submission.messageId &&
        latestMessages.current.some((m) => m.id === submission.messageId);
      if (result.saved || recovered) {
        if (!result.saved && recovered) onRecoveredDelivery();
        if (sessionStorage.getItem(storageKey) === text)
          sessionStorage.removeItem(storageKey);
        if (pending.current === submission) pending.current = null;
      } else {
        // Restore the draft through the native composer; the API still owns delivery truth.
        throw new MessageNotSentError("尚未确认保存，请检查提示后重试。");
      }
    },
  });
  useEffect(() => {
    const sent = pending.current;
    if (!sent?.messageId || !messages.some((m) => m.id === sent.messageId))
      return;
    if (sessionStorage.getItem(storageKey) === sent.text)
      sessionStorage.removeItem(storageKey);
    if (runtime.thread.composer.getState().text === sent.text)
      runtime.thread.composer.setText("");
    pending.current = null;
    onRecoveredDelivery();
  }, [messages, runtime, storageKey, onRecoveredDelivery]);
  return (
    <AssistantRuntimeProvider runtime={runtime}>
      {children}
    </AssistantRuntimeProvider>
  );
}
