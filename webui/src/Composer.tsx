import { ComposerPrimitive, useAui } from "@assistant-ui/react";
import { useEffect, useLayoutEffect, useRef } from "react";

export type ComposerMention = {
  runId: string;
  path: string;
  nonce: number;
};

export function Composer({
  cacheKey,
  disabled,
  hint,
  question,
  stop,
  mention,
}: {
  cacheKey: string;
  disabled: boolean;
  hint?: string | null;
  question?: string;
  stop?: { enabled: boolean; requested: boolean; onStop: () => void };
  mention?: ComposerMention | null;
}) {
  const aui = useAui();
  const storageKey = `abaqus-draft:${cacheKey}`;
  const draft = useRef("");
  useLayoutEffect(() => {
    const saved = sessionStorage.getItem(storageKey) ?? "";
    draft.current = saved;
    aui.composer().setText(saved);
  }, [aui, storageKey]);
  useEffect(() => {
    if (!mention) return;
    const token = `@${mention.path}`;
    const current = draft.current;
    const separator = current && !current.endsWith(" ") && !current.endsWith("\n") ? " " : "";
    const next = `${current}${separator}${token}`;
    draft.current = next;
    sessionStorage.setItem(storageKey, next);
    aui.composer().setText(next);
    requestAnimationFrame(() => document.getElementById("message-input")?.focus());
  }, [aui, mention?.nonce, storageKey]);
  return (
    <ComposerPrimitive.Root className="composer">
      {question && (
        <div className="question-context">
          <strong>需要你补充</strong>
          <p>{question}</p>
        </div>
      )}
      <label htmlFor="message-input">
        {question ? "回复当前问题" : "消息"}
      </label>
      <ComposerPrimitive.Input
        id="message-input"
        aria-label={question ? "回复当前问题" : "消息"}
        readOnly={disabled}
        maxLength={20000}
        minRows={3}
        submitMode="enter"
        addAttachmentOnPaste={false}
        cancelOnEscape={false}
        unstable_focusOnRunStart={false}
        placeholder={
          question
            ? "输入你的答复…"
            : cacheKey === "new"
              ? "发消息…"
              : "继续追问，或补充你的要求…"
        }
        onChange={(event) => {
          draft.current = event.currentTarget.value;
          sessionStorage.setItem(storageKey, event.currentTarget.value);
        }}
      />
      <div className="row">
        <small className="muted">
          {hint || "Enter 发送 · Shift+Enter 换行"}
        </small>
        {stop ? (
          <button
            type="button"
            disabled={!stop.enabled || stop.requested}
            onClick={stop.onStop}
            title="停止当前模型回复；不取消Abaqus运行"
          >
            {stop.requested ? "正在停止…" : "停止回复"}
          </button>
        ) : (
          <ComposerPrimitive.Send>
            {disabled ? "发送中…" : "发送"}
          </ComposerPrimitive.Send>
        )}
      </div>
    </ComposerPrimitive.Root>
  );
}
