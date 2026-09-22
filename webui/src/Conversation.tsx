import {
  ActionBarPrimitive,
  useAuiState,
  MessagePrimitive,
  ThreadPrimitive,
  type ToolCallMessagePartProps,
  type ThreadMessageLike,
} from "@assistant-ui/react";
import {
  MarkdownTextPrimitive,
  type CodeHeaderProps,
} from "@assistant-ui/react-markdown";
import { createContext, useContext, useState } from "react";
import type { Artifact, View, Message, ToolSummary } from "./api/client";
import { ThinkingIcon } from "./ThinkingIcon";
import { TurnWrittenFiles } from "./TurnWrittenFiles";
import { ModelPreview } from "./ModelPreview";
import { ViewportPreview } from "./ViewportPreview";

const names: Record<string, string> = {
  ask_user: "补充信息",
  run_cae_script: "CAE 建模",
  run_solver_job: "Standard 求解",
  solver_job: "Standard 求解",
  run_abaqus_python: "结果提取",
  write_file: "写入文件",
  edit_file: "修改文件",
  read_file: "读取文件",
};
const parse = (value: string | undefined | null) => {
  if (value == null) return undefined;
  try {
    return JSON.parse(value);
  } catch {
    return value;
  }
};
const pretty = (value: unknown) =>
  typeof value === "string" ? value : JSON.stringify(value, null, 2);
const ConversationRenderContext = createContext<{
  onOpenFile: (path: string) => void;
  runId: string;
  files: readonly Artifact[];
  canPreview: boolean;
} | null>(null);

function useConversationRenderContext() {
  const context = useContext(ConversationRenderContext);
  if (!context) throw new Error("Conversation render context is unavailable");
  return context;
}

function Tool({
  toolName,
  args,
  result,
  status,
  onOpenFile,
  runId,
  files,
  canPreview,
}: ToolCallMessagePartProps & {
  onOpenFile: (path: string) => void;
  runId: string;
  files: readonly Artifact[];
  canPreview: boolean;
}) {
  const hostSummary = useAuiState(
    (s) => s.message.metadata.custom?.toolSummary,
  ) as ToolSummary | undefined;
  const runtime = [
    "run_cae_script",
    "run_solver_job",
    "solver_job",
    "run_abaqus_python",
  ].includes(toolName);
  const record: Record<string, unknown> =
    result && typeof result === "object"
      ? (result as Record<string, unknown>)
      : {};
  const stage = hostSummary?.stage_succeeded ?? record.stage_succeeded;
  const active = result === undefined && status.type !== "incomplete";
  const failed =
    stage === false ||
    record.succeeded === false ||
    status.type === "incomplete";
  const label =
    toolName === "ask_user"
      ? failed
        ? "提问未生效"
        : result === undefined
          ? "等待答复"
          : "已回答"
      : result === undefined
        ? "尚无返回"
        : failed
          ? "未完成"
          : runtime && stage === true
            ? "阶段证据通过"
            : "已有返回";
  const target =
    hostSummary?.target ??
    (typeof args.script_path === "string"
      ? args.script_path
      : typeof args.input_file === "string"
        ? args.input_file
        : typeof args.path === "string"
          ? args.path
          : typeof args.question === "string"
            ? args.question
            : null);
  const duration = hostSummary?.duration_seconds ?? record.duration_seconds;
  const changed = new Set(
    (hostSummary?.changed_paths ?? []).map((path) =>
      path.replaceAll("\\", "/").toLowerCase(),
    ),
  );
  const modelArtifact =
    toolName === "run_cae_script"
      ? files.find((file) => {
          const path = file.relative_path.replaceAll("\\", "/").toLowerCase();
          return file.fresh && path.endsWith(".obj") && changed.has(path);
        })
      : undefined;
  const viewportArtifact =
    toolName === "run_cae_script"
      ? files.find((file) => {
          const path = file.relative_path.replaceAll("\\", "/").toLowerCase();
          return file.fresh && path.endsWith(".png") && changed.has(path);
        })
      : undefined;
  return (
    <>
      <div className={`tool-card ${failed ? "tool-failed" : ""}`}>
        <div className="tool-heading">
          <span className="tool-heading-main">
            <ThinkingIcon active={active} />
            <strong>{names[toolName] ?? toolName}</strong>
          </span>
          <span className="tool-state">
            {label}
            {typeof duration === "number" && ` · ${duration.toFixed(1)} 秒`}
          </span>
        </div>
        {target && (
          <p className="tool-target" title={target}>
            {target}
          </p>
        )}
        <div className="tool-facts">
          {(hostSummary?.timed_out || record.timed_out === true) && (
            <span>已超时</span>
          )}
          {!!hostSummary?.warning_count && (
            <span className="warning">警告 {hostSummary.warning_count} 条</span>
          )}
        </div>
        {hostSummary?.error && <p className="tool-error">{hostSummary.error}</p>}
        {!!hostSummary?.changed_count && (
          <TurnWrittenFiles
            paths={hostSummary.changed_paths ?? []}
            total={hostSummary.changed_count}
            onOpenFile={onOpenFile}
          />
        )}
        <details className="tool-record">
          <summary>查看输入与返回</summary>
          <div>
            <p className="eyebrow">输入</p>
            <pre>{pretty(args)}</pre>
            {result !== undefined && (
              <>
                <p className="eyebrow">返回记录（含可用日志摘要）</p>
                <pre>{pretty(result)}</pre>
              </>
            )}
          </div>
        </details>
      </div>
      {canPreview && modelArtifact && (
        <ModelPreview runId={runId} artifact={modelArtifact} />
      )}
      {canPreview && viewportArtifact && (
        <ViewportPreview runId={runId} artifact={viewportArtifact} />
      )}
    </>
  );
}
function ToolFallback(props: ToolCallMessagePartProps) {
  const { onOpenFile, runId, files, canPreview } = useConversationRenderContext();
  return (
    <Tool
      {...props}
      onOpenFile={onOpenFile}
      runId={runId}
      files={files}
      canPreview={canPreview}
    />
  );
}
function CodeHeader({ language, code }: CodeHeaderProps) {
  const [copied, setCopied] = useState(false);
  async function copy() {
    await navigator.clipboard.writeText(code);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1600);
  }
  return (
    <div className="code-header">
      <span>{language || "代码"}</span>
      <button type="button" className="subtle" onClick={() => void copy()}>
        {copied ? "已复制" : "复制代码"}
      </button>
    </div>
  );
}
function Text() {
  return (
    <MarkdownTextPrimitive
      className="markdown"
      components={{ CodeHeader }}
    />
  );
}
function UserMessage() {
  return (
    <MessagePrimitive.Root className="message user">
      <span className="eyebrow">用户输入</span>
      <MessagePrimitive.Parts components={{ Text }} />
    </MessagePrimitive.Root>
  );
}
function ReplyActions() {
  const hasText = useAuiState((s) =>
    s.message.content.some((part) => part.type === "text" && !!part.text),
  );
  const copied = useAuiState((s) => s.message.isCopied);
  const running = useAuiState((s) => s.message.status?.type === "running");
  const incomplete = useAuiState(
    (s) => s.message.status?.type === "incomplete",
  );
  if (!hasText && !incomplete && !running) return null;
  return (
    <div className="reply-actions">
      {running && (
        <small className="reply-status">
          <ThinkingIcon active />
          生成中
        </small>
      )}
      {incomplete && <small className="muted">回复未完成</small>}
      {hasText && (
        <ActionBarPrimitive.Copy className="subtle" copiedDuration={2000}>
          {copied ? "已复制" : "复制回复"}
        </ActionBarPrimitive.Copy>
      )}
    </div>
  );
}
function AssistantMessage() {
  return (
    <MessagePrimitive.Root className="message assistant">
      <MessagePrimitive.Parts
        components={{
          Text,
          tools: {
            Fallback: ToolFallback,
          },
        }}
      />
      <ReplyActions />
    </MessagePrimitive.Root>
  );
}
function convert(message: Message): ThreadMessageLike {
  if (message.kind !== "tool")
    return {
      id: message.id,
      role: message.role,
      content: message.text ?? "",
      ...(message.role === "assistant" && message.status === "interrupted"
        ? {
            status: {
              type: "incomplete" as const,
              reason: "cancelled" as const,
            },
          }
        : {}),
    };
  const args = parse(message.arguments);
  return {
    id: message.id,
    role: "assistant",
    metadata: { custom: { toolSummary: message.tool_summary } },
    content: [
      {
        type: "tool-call",
        toolCallId: message.tool_call_id ?? message.id,
        toolName: message.tool_name ?? "tool",
        args:
          typeof args === "object" && args !== null ? args : { value: args },
        argsText: message.arguments ?? "{}",
        ...(message.output != null
          ? {
              result: parse(message.output),
              isError: message.status === "failed",
            }
          : {}),
      },
    ],
  };
}
export function toMessages(view: View): ThreadMessageLike[] {
  const result = (view.messages ?? []).map(convert);
  if (view.live_reply?.text)
    result.push({
      id: `live:${view.live_reply.request_id}`,
      role: "assistant",
      content: view.live_reply.text,
      status: { type: "running" },
    });
  return result;
}
export function Conversation({
  onOpenFile,
  runId,
  files,
  canPreview,
}: {
  onOpenFile: (path: string) => void;
  runId: string;
  files: readonly Artifact[];
  canPreview: boolean;
}) {
  return (
    <ConversationRenderContext.Provider
      value={{ onOpenFile, runId, files, canPreview }}
    >
      <ThreadPrimitive.Root className="thread">
        <ThreadPrimitive.Viewport className="messages" autoScroll>
          <ThreadPrimitive.Empty>
            <p className="muted">这里显示回复和工具记录。</p>
          </ThreadPrimitive.Empty>
          <ThreadPrimitive.Messages components={{ UserMessage, AssistantMessage }} />
          <ThreadPrimitive.ScrollToBottom
            className="scroll-to-bottom"
            aria-label="回到最新消息"
          >
            回到最新消息
          </ThreadPrimitive.ScrollToBottom>
        </ThreadPrimitive.Viewport>
      </ThreadPrimitive.Root>
    </ConversationRenderContext.Provider>
  );
}
