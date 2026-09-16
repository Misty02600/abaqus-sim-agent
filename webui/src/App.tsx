import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
} from "react";
import {
  authenticate,
  request,
  runPath,
  errorText,
  commandId,
  clearCommand,
  type Snapshot,
} from "./api/client";
import { useRun } from "./useRun";
import { Conversation } from "./Conversation";
import { Artifacts } from "./Artifacts";
import { Composer, type ComposerMention } from "./Composer";
import { WorkbenchRuntime, type Delivery } from "./WorkbenchRuntime";
import { ConversationList } from "./ConversationList";
import { Icon } from "./Icon";
import { SidePanel } from "./SidePanel";
import { RunDetails } from "./RunDetails";
import { RunSummary } from "./RunSummary";
import { RunStatusBar } from "./RunStatusBar";
import { CommandPalette, type QuickAction } from "./CommandPalette";
import { WorkspaceExplorer } from "./WorkspaceExplorer";
import { ThreadListPrimitive } from "@assistant-ui/react";

const SIDEBAR_MIN = 220;
const SIDEBAR_MAX = 520;
const SIDEBAR_DEFAULT = 280;
const SIDEBAR_STORAGE_KEY = "abaqus-sidebar-width";

function clampSidebarWidth(value: number) {
  return Math.max(SIDEBAR_MIN, Math.min(SIDEBAR_MAX, value));
}

function initialSidebarWidth() {
  const saved = Number(localStorage.getItem(SIDEBAR_STORAGE_KEY));
  return Number.isFinite(saved) && saved > 0
    ? clampSidebarWidth(saved)
    : SIDEBAR_DEFAULT;
}

function selectedId() {
  const match = location.pathname.match(/^\/runs\/(web-[0-9a-f]{32})$/);
  return match?.[1] ?? null;
}
export default function App() {
  const [unavailable, setUnavailable] = useState<
      { run_id: string; message: string }[]
    >([]),
    [ready, setReady] = useState(false),
    [authError, setAuthError] = useState("");
  const [renaming, setRenaming] = useState(false),
    [newTitle, setNewTitle] = useState("");
  const [id, setId] = useState<string | null>(selectedId),
    [runs, setRuns] = useState<Snapshot[]>([]),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const {
    snapshot,
    error: readError,
    connected,
    refresh,
  } = useRun(ready ? id : null);
  const [desktop, setDesktop] = useState(
    () => window.matchMedia("(min-width: 900px)").matches,
  );
  const [sidebarOpen, setSidebarOpen] = useState(desktop);
  const [sidebarWidth, setSidebarWidth] = useState(initialSidebarWidth);
  const [filesOpen, setFilesOpen] = useState(true),
    [artifactsOpen, setArtifactsOpen] = useState(false),
    [detailsOpen, setDetailsOpen] = useState(false),
    [query, setQuery] = useState("");
  const [searchOpen, setSearchOpen] = useState(false),
    [commandOpen, setCommandOpen] = useState(false),
    [notice, setNotice] = useState(""),
    [selectedArtifactPath, setSelectedArtifactPath] = useState<string | null>(null);
  const [composerMention, setComposerMention] = useState<ComposerMention | null>(null);
  const shortcutActions = useRef<QuickAction[]>([]);
  useEffect(() => {
    if (!notice) return;
    const timer = setTimeout(() => setNotice(""), 2500);
    return () => clearTimeout(timer);
  }, [notice]);
  useEffect(() => {
    if (!ready) return;
    const keydown = (event: KeyboardEvent) => {
      if (event.isComposing) return;
      if (
        (event.ctrlKey || event.metaKey) &&
        !event.altKey &&
        event.key.toLowerCase() === "k"
      ) {
        event.preventDefault();
        if (commandOpen) setCommandOpen(false);
        else if (!document.querySelector("dialog[open]")) setCommandOpen(true);
        return;
      }
      const editing =
        event.target instanceof Element &&
        !!event.target.closest(
          'input,textarea,select,[contenteditable="true"]',
        );
      if (
        editing ||
        document.querySelector("dialog[open]") ||
        event.ctrlKey ||
        event.metaKey ||
        event.altKey
      )
        return;
      const id =
        event.key.toLowerCase() === "n"
          ? "new"
          : event.key.toLowerCase() === "r"
            ? "refresh"
            : null;
      const action = shortcutActions.current.find(
        (item) => item.id === id && !item.disabled,
      );
      if (action) {
        event.preventDefault();
        void Promise.resolve(action.run()).catch((error) =>
          setError(errorText(error)),
        );
      }
    };
    addEventListener("keydown", keydown);
    return () => removeEventListener("keydown", keydown);
  }, [ready, commandOpen]);
  const visibleRuns = runs.filter((run) =>
    run.title.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()),
  );
  useEffect(() => {
    const desktop = window.matchMedia("(min-width: 900px)");
    const update = () => {
      setDesktop(desktop.matches);
      setSidebarOpen(desktop.matches);
    };
    desktop.addEventListener("change", update);
    return () => desktop.removeEventListener("change", update);
  }, []);
  useEffect(() => {
    setDetailsOpen(false);
    setFilesOpen(true);
    setArtifactsOpen(false);
    setSelectedArtifactPath(null);
    setComposerMention(null);
  }, [id]);
  const selected = useRef(id);
  selected.current = id;
  const listSequence = useRef(0);
  const list = useCallback(async () => {
    const order = ++listSequence.current;
    try {
      const result = await request<{
        runs: Snapshot[];
        unavailable: { run_id: string; message: string }[];
      }>("/runs");
      if (order !== listSequence.current) return;
      setRuns(result.runs);
      setUnavailable(result.unavailable);
    } catch (error) {
      if (order === listSequence.current) setError(errorText(error));
    }
  }, []);
  useEffect(() => {
    const connect = () => {
      setReady(false);
      setAuthError("");
      void authenticate()
        .then(() => {
          setReady(true);
          void list();
        })
        .catch((e) => setAuthError(errorText(e)));
    };
    connect();
    const changed = () => {
      if (location.hash.startsWith("#session=")) connect();
    };
    addEventListener("hashchange", changed);
    return () => removeEventListener("hashchange", changed);
  }, [list]);
  useEffect(() => {
    const pop = () => setId(selectedId());
    addEventListener("popstate", pop);
    return () => removeEventListener("popstate", pop);
  }, []);
  useEffect(() => {
    setRenaming(false);
    setError("");
  }, [id, snapshot?.view.question?.question_id, snapshot?.view.task_revision]);
  useEffect(() => {
    if (ready) void list();
  }, [snapshot?.version, ready, list]);
  useEffect(() => {
    if (!ready) return;
    const timer = window.setInterval(() => void list(), 2500);
    return () => window.clearInterval(timer);
  }, [ready, list]);
  function navigate(next: string | null) {
    if (next === null) setQuery("");
    if (!window.matchMedia("(min-width: 900px)").matches) setSidebarOpen(false);
    if (next === id) return;
    history.pushState(null, "", next ? `/runs/${next}` : "/");
    setId(next);
  }
  async function create(goal: string): Promise<Delivery> {
    if (busy) return { saved: false };
    let saved = false;
    let messageId: string | undefined;
    setBusy(true);
    setError("");
    try {
      const key = commandId("new", "create", goal);
      messageId = `web-${key.replaceAll("-", "")}:goal`;
      const next = await request<Snapshot>("/runs", {
        idempotency_key: key,
        goal,
      });
      clearCommand("new", "create");
      saved = true;
      navigate(next.view.run_id);
      const startKey = commandId(next.view.run_id, "start");
      await request(runPath(next.view.run_id) + "/start", {
        command_id: startKey,
      });
      clearCommand(next.view.run_id, "start");
      await list();
      return { saved: true, messageId };
    } catch (error) {
      setError(errorText(error));
      return { saved, messageId };
    } finally {
      setBusy(false);
    }
  }
  async function act(action: "start" | "continue") {
    if (!snapshot || busy) return;
    setBusy(true);
    setError("");
    try {
      const key = commandId(snapshot.view.run_id, action);
      const reply = await request<{ error?: string | null }>(
        runPath(snapshot.view.run_id) + "/" + action,
        { command_id: key },
      );
      clearCommand(snapshot.view.run_id, action);
      if (reply.error && selected.current === snapshot.view.run_id)
        setError(reply.error);
      await refresh();
      await list();
    } catch (error) {
      if (selected.current === snapshot.view.run_id) setError(errorText(error));
      await refresh();
    } finally {
      setBusy(false);
    }
  }
  async function send(text: string): Promise<Delivery> {
    if (!snapshot || busy) return { saved: false };
    const runId = snapshot.view.run_id;
    let messageId: string | undefined;
    const answering = snapshot.view.question?.pending === true;
    const action = answering ? "answers" : "messages";
    const payload = answering
      ? {
          question_id: snapshot.view.question!.question_id,
          expected_task_revision: snapshot.view.task_revision ?? 0,
          answer: text,
        }
      : {
          expected_task_revision: snapshot.view.task_revision ?? 0,
          expected_amendment_sequence: snapshot.view.amendment_sequence ?? 0,
          text,
        };
    setBusy(true);
    setError("");
    try {
      const key = commandId(runId, action, JSON.stringify(payload));
      messageId = answering
        ? `${runId}:answer:${snapshot.view.question!.question_id}`
        : `${runId}:message:${key.replaceAll("-", "")}`;
      const reply = await request<{
        message_saved?: boolean;
        answer_saved: boolean;
        error?: string | null;
      }>(runPath(runId) + "/" + action, { command_id: key, ...payload });
      const saved = answering ? reply.answer_saved : !!reply.message_saved;
      if (saved) clearCommand(runId, action);
      if (selected.current === runId) {
        if (reply.error) setError(reply.error);
        else if (!saved) setError("尚未确认输入已保存，请保留草稿并刷新检查。");
      }
      await refresh();
      await list();
      return { saved, messageId };
    } catch (error) {
      if (selected.current === runId) setError(errorText(error));
      await refresh();
      return { saved: false, messageId };
    } finally {
      setBusy(false);
    }
  }
  async function renameRun(
    runId: string,
    title: string,
    titleVersion: number,
  ): Promise<boolean> {
    if (busy || !title.trim()) return false;
    setBusy(true);
    setError("");
    const payload = {
      title: title.trim(),
      expected_title_version: titleVersion,
    };
    try {
      const key = commandId(
        runId,
        "title",
        JSON.stringify(payload),
      );
      await request(runPath(runId) + "/title", {
        command_id: key,
        ...payload,
      });
      clearCommand(runId, "title");
      if (selected.current === runId) await refresh();
      await list();
      return true;
    } catch (error) {
      setError(errorText(error));
      return false;
    } finally {
      setBusy(false);
    }
  }
  async function rename() {
    if (!snapshot || !newTitle.trim()) return;
    const saved = await renameRun(
      snapshot.view.run_id,
      newTitle,
      snapshot.title_version ?? 0,
    );
    if (saved) setRenaming(false);
  }
  async function stop() {
    if (!snapshot?.model_request_id || busy || !snapshot.can_stop) return;
    setBusy(true);
    setError("");
    try {
      const key = commandId(
        snapshot.view.run_id,
        "stop",
        snapshot.model_request_id,
      );
      await request(runPath(snapshot.view.run_id) + "/stop", {
        command_id: key,
        expected_request_id: snapshot.model_request_id,
      });
      clearCommand(snapshot.view.run_id, "stop");
      await refresh();
    } catch (error) {
      setError(errorText(error));
      await refresh();
    } finally {
      setBusy(false);
    }
  }
  async function cancelSolver() {
    const execution = snapshot?.tool_execution;
    if (!snapshot || !execution || busy || !snapshot.can_cancel_tool_execution)
      return;
    const runId = snapshot.view.run_id;
    setBusy(true);
    setError("");
    try {
      const action = `cancel-tool-${execution.execution_id}`;
      const key = commandId(runId, action);
      await request(
        runPath(runId) +
          `/tool-executions/${encodeURIComponent(execution.execution_id)}/cancel`,
        { command_id: key },
      );
      clearCommand(runId, action);
      await refresh();
      await list();
    } catch (error) {
      if (selected.current === runId) setError(errorText(error));
      await refresh();
    } finally {
      setBusy(false);
    }
  }
  function openRename() {
    if (!snapshot || busy) return;
    setNewTitle(snapshot.title.slice(0, 120));
    setRenaming(true);
  }
  async function refreshCurrent() {
    await Promise.all([refresh(), list()]);
  }
  function showFiles() {
    setSidebarOpen(true);
    setFilesOpen(true);
  }
  function mentionWorkspaceFile(path: string) {
    if (!snapshot) return;
    setComposerMention((current) => ({
      runId: snapshot.view.run_id,
      path,
      nonce: (current?.nonce ?? 0) + 1,
    }));
  }
  function openArtifact(path: string) {
    if (!snapshot?.view.files?.some((file) => file.relative_path === path)) {
      setError(`当前修订的可见文件中找不到 ${path}`);
      return;
    }
    setSidebarOpen(true);
    setArtifactsOpen(true);
    setSelectedArtifactPath(path);
  }
  function startSidebarResize(event: ReactPointerEvent<HTMLDivElement>) {
    if (!desktop || !sidebarOpen) return;
    event.preventDefault();
    const startX = event.clientX;
    const startWidth = sidebarWidth;
    document.body.classList.add("sidebar-resizing");
    const move = (pointer: PointerEvent) => {
      const next = clampSidebarWidth(startWidth + pointer.clientX - startX);
      setSidebarWidth(next);
      localStorage.setItem(SIDEBAR_STORAGE_KEY, String(next));
    };
    const stopResize = () => {
      document.body.classList.remove("sidebar-resizing");
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", stopResize);
      window.removeEventListener("pointercancel", stopResize);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", stopResize);
    window.addEventListener("pointercancel", stopResize);
  }
  const actions: QuickAction[] = [
    {
      id: "new",
      label: "新建会话",
      shortcut: "N",
      disabled: busy,
      run: () => navigate(null),
    },
    {
      id: "search",
      label: "搜索会话名称",
      keywords: "会话 查找",
      run: () => {
        setSidebarOpen(true);
        setSearchOpen(true);
      },
    },
    {
      id: "focus",
      label: "回到消息输入框",
      disabled: !!id && !snapshot,
      run: () => {
        requestAnimationFrame(() =>
          document.getElementById("message-input")?.focus(),
        );
      },
    },
    {
      id: "refresh",
      label: "刷新当前状态",
      shortcut: "R",
      run: refreshCurrent,
    },
    {
      id: "files",
      label: "查看工作区文件",
      keywords: "产物 日志 图片",
      disabled: !snapshot,
      run: showFiles,
    },
    {
      id: "details",
      label: "查看运行详情",
      keywords: "验收 结果 模型 推理 预算 配置",
      disabled: !snapshot,
      run: () => setDetailsOpen(true),
    },
    {
      id: "rename",
      label: "重命名会话",
      disabled: !snapshot || busy,
      run: openRename,
    },
    {
      id: "copy",
      label: "复制会话 ID",
      disabled: !snapshot,
      run: async () => {
        if (snapshot) {
          await navigator.clipboard.writeText(snapshot.view.run_id);
          setNotice("已复制会话 ID");
        }
      },
    },
    {
      id: "continue",
      label: "继续上次执行",
      disabled: busy || !snapshot?.can_continue,
      hint: snapshot?.can_continue
        ? "继续已有上下文，保留累计用量"
        : "当前状态不允许继续执行",
      run: () => act("continue"),
    },
    {
      id: "stop",
      label: "停止模型回复",
      disabled: busy || !snapshot?.can_stop,
      hint: "仅模型回复阶段；不取消Abaqus作业",
      run: stop,
    },
    {
      id: "cancel-solver",
      label: "取消当前 Solver 作业",
      keywords: "停止 求解 Abaqus Standard",
      disabled: busy || !snapshot?.can_cancel_tool_execution,
      hint: "终止当前宿主控制的 Solver 进程树",
      run: cancelSolver,
    },
  ];
  shortcutActions.current = actions;
  if (!ready)
    return (
      <main className="welcome">
        <div className="brandmark">A</div>
        <h1>Abaqus 工作台</h1>
        <p>{authError || "正在连接本地服务…"}</p>
        {authError && (
          <p className="muted">请使用启动终端显示的完整会话链接打开本页。</p>
        )}
      </main>
    );
  const view = snapshot?.view;
  const draftKey = id
    ? `${id}:${view?.question?.pending ? view.question.question_id : "message"}`
    : "new";
  return (
    <WorkbenchRuntime
      key={draftKey}
      id={id}
      snapshot={snapshot}
      runs={visibleRuns}
      busy={busy}
      draftKey={draftKey}
      onSend={id ? send : create}
      onNavigate={navigate}
      onRecoveredDelivery={() => {
        if (selected.current === id) setError("");
      }}
    >
      <div
        className={`app ${sidebarOpen ? "" : "sidebar-collapsed"}`}
        style={
          desktop && sidebarOpen
            ? { gridTemplateColumns: `${sidebarWidth}px minmax(0, 1fr)` }
            : undefined
        }
      >
        {sidebarOpen && (
          <button
            className="sidebar-scrim"
            aria-label="关闭侧栏"
            onClick={() => setSidebarOpen(false)}
          />
        )}
        <aside className="sidebar" id="workspace-sidebar">
          <ConversationList
            busy={busy}
            query={query}
            onQueryChange={setQuery}
            empty={visibleRuns.length === 0}
            searching={searchOpen}
            onSearchChange={setSearchOpen}
            workspace={snapshot?.workspace ?? null}
            onRename={renameRun}
          />
          {unavailable.map((item) => (
            <p key={item.run_id} className="error small">
              {item.message}
            </p>
          ))}
          <section
            className={`files-section ${filesOpen ? "" : "is-collapsed"}`}
          >
            <div className="files-heading">
              <button
                className="section-toggle"
                aria-expanded={filesOpen}
                onClick={() => setFilesOpen(!filesOpen)}
              >
                <Icon name="chevron" />
                <span>工作区文件</span>
                <small>{snapshot?.view.files?.length ?? 0}</small>
              </button>
              {snapshot && (
                <button
                  className="icon-button"
                  aria-label="刷新文件记录"
                  onClick={() => void refreshCurrent()}
                >
                  <Icon name="refresh" />
                </button>
              )}
            </div>
            {filesOpen &&
              (snapshot ? (
                <WorkspaceExplorer
                  runId={snapshot.view.run_id}
                  onMention={mentionWorkspaceFile}
                />
              ) : (
                <p className="empty">选择会话后查看文件</p>
              ))}
          </section>
          <section
            className={`files-section artifacts-section ${artifactsOpen ? "" : "is-collapsed"}`}
          >
            <div className="files-heading">
              <button
                className="section-toggle"
                aria-expanded={artifactsOpen}
                onClick={() => setArtifactsOpen(!artifactsOpen)}
              >
                <Icon name="chevron" />
                <span>任务产物</span>
                <small>{snapshot?.view.files?.length ?? 0}</small>
              </button>
            </div>
            {artifactsOpen &&
              (snapshot ? (
                <Artifacts
                  snapshot={snapshot}
                  selectedPath={selectedArtifactPath}
                  onSelectedPathChange={setSelectedArtifactPath}
                />
              ) : (
                <p className="empty">选择会话后查看产物</p>
              ))}
          </section>
          <div
            className="sidebar-resizer"
            role="separator"
            aria-label="调整侧栏宽度"
            aria-orientation="vertical"
            aria-valuemin={SIDEBAR_MIN}
            aria-valuemax={SIDEBAR_MAX}
            aria-valuenow={sidebarWidth}
            onPointerDown={startSidebarResize}
          />
        </aside>
        <div className="main-area">
          <header className="toolbar">
            <button
              className="icon-button"
              aria-label={sidebarOpen ? "收起侧栏" : "展开侧栏"}
              aria-expanded={sidebarOpen}
              aria-controls="workspace-sidebar"
              onClick={() => setSidebarOpen(!sidebarOpen)}
            >
              <Icon name="panel" />
            </button>
            {!sidebarOpen && (
              <ThreadListPrimitive.New
                className="icon-button"
                disabled={busy}
                aria-label="新建会话"
              >
                <Icon name="plus" />
              </ThreadListPrimitive.New>
            )}
            <button
              className="toolbar-title"
              title={snapshot?.title}
              disabled={!snapshot || busy}
              aria-label="重命名会话"
              onClick={openRename}
            >
              {snapshot?.title.split("\n")[0] ?? "新会话"}
            </button>
            <button
              className="toolbar-action command-trigger"
              aria-label="打开快速操作"
              onClick={() => setCommandOpen(true)}
            >
              <kbd>Ctrl K</kbd>
            </button>
            {notice && (
              <span
                className="toolbar-notice"
                role="status"
                aria-label="操作反馈"
              >
                {notice}
              </span>
            )}
            <button
              className="toolbar-action"
              disabled={!snapshot}
              aria-label="查看工作区文件"
              onClick={showFiles}
            >
              <Icon name="folder" />
              <span>文件</span>
            </button>
            <button
              className="toolbar-action"
              disabled={!snapshot}
              aria-label="查看运行详情"
              onClick={() => setDetailsOpen(true)}
            >
              <Icon name="info" />
              <span>详情</span>
            </button>
          </header>
          <main>
            {!id ? (
              <section className="new-task">
                <Composer cacheKey="new" disabled={busy} />
              </section>
            ) : !snapshot ? (
              <p className="empty">{readError || "正在读取任务…"}</p>
            ) : (
              <div className="workspace">
                <section className="conversation">
                  <RunSummary
                    snapshot={snapshot}
                    onDetails={() => setDetailsOpen(true)}
                  />
                  {renaming && (
                    <form
                      className="rename-form"
                      onSubmit={(e) => {
                        e.preventDefault();
                        void rename();
                      }}
                    >
                      <label htmlFor="conversation-title">会话名称</label>
                      <input
                        id="conversation-title"
                        value={newTitle}
                        onChange={(e) => setNewTitle(e.target.value)}
                        maxLength={120}
                        autoFocus
                        onKeyDown={(e) => {
                          if (e.key === "Escape") setRenaming(false);
                        }}
                      />
                      <div className="row">
                        <button disabled={busy || !newTitle.trim()}>
                          保存名称
                        </button>
                        <button
                          type="button"
                          className="subtle"
                          onClick={() => setRenaming(false)}
                        >
                          取消
                        </button>
                      </div>
                    </form>
                  )}
                  <Conversation
                    onOpenFile={openArtifact}
                    runId={snapshot.view.run_id}
                    files={snapshot.view.files ?? []}
                    canPreview={!snapshot.active}
                  />
                  {snapshot.can_start && (
                    <button disabled={busy} onClick={() => void act("start")}>
                      重试发送首条消息
                    </button>
                  )}
                  {snapshot.can_continue && (
                    <button
                      disabled={busy}
                      onClick={() => void act("continue")}
                    >
                      继续上次执行
                    </button>
                  )}
                  <Composer
                    cacheKey={draftKey}
                    disabled={busy}
                    mention={
                      composerMention?.runId === snapshot.view.run_id
                        ? composerMention
                        : null
                    }
                    question={
                      view!.question?.pending
                        ? view!.question.question
                        : undefined
                    }
                    stop={
                      snapshot.active
                        ? {
                            enabled: !!snapshot.can_stop && !busy,
                            requested: !!snapshot.stop_requested,
                            onStop: () => void stop(),
                          }
                        : undefined
                    }
                    hint={
                      view!.question?.pending
                        ? snapshot.can_answer
                          ? undefined
                          : "正在处理，请稍候。"
                        : snapshot.message_block_reason
                    }
                  />
                  <RunStatusBar
                    snapshot={snapshot}
                    connected={connected}
                    onDetails={() => setDetailsOpen(true)}
                    onCancelSolver={() => void cancelSolver()}
                    cancellingSolver={busy}
                  />
                  {(error || readError || view!.error) && (
                    <p role="alert" className="error">
                      {error || readError || view!.error}
                    </p>
                  )}
                  {snapshot.command_uncertain && (
                    <p className="muted">
                      上次命令的回执未完成，后续操作仍需由宿主核对。
                    </p>
                  )}
                </section>
              </div>
            )}
            {!id && error && (
              <p role="alert" className="error">
                {error}
              </p>
            )}
          </main>
        </div>
        {commandOpen && (
          <CommandPalette
            actions={actions}
            onClose={() => setCommandOpen(false)}
            onError={(error) => setError(errorText(error))}
          />
        )}
        {detailsOpen && snapshot && (
          <SidePanel title="运行详情" onClose={() => setDetailsOpen(false)}>
            <RunDetails snapshot={snapshot} />
          </SidePanel>
        )}
      </div>
    </WorkbenchRuntime>
  );
}
