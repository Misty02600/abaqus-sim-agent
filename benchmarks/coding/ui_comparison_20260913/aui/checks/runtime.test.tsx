// @vitest-environment jsdom
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react";
import { AgUiArm } from "../src/adapters/AgUiArm";
import { ExternalArm } from "../src/adapters/ExternalArm";
import { type Snapshot } from "../src/api";
import { hostRepository } from "../src/adapters/aguiHistory";
import { useHost } from "../src/useHost";

beforeAll(() => {
  globalThis.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
  Element.prototype.scrollTo = () => {};
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
const pending = (): Snapshot => ({
  run_id: "host-run-001", arm: "agui", goal: "Minimal task", status: "waiting_for_user", task_revision: 0, phase: "clarification",
  question: { question_id: "q001", question: "What is the plate thickness?", pending: true, answer: null }, outcome: null, verification: null,
  messages: {
    agui: [{ id: "user", role: "user", content: "Minimal task" }, { id: "assistant", role: "assistant", content: "Please clarify.", toolCalls: [{ id: "q001", type: "function", function: { name: "ask_user", arguments: '{"question":"What is the plate thickness?"}' } }] }],
    aui: [{ id: "user", role: "user", content: "Minimal task" }, { id: "assistant", role: "assistant", content: [{ type: "text", text: "Please clarify." }, { type: "tool-call", toolCallId: "q001", toolName: "ask_user", args: { question: "What is the plate thickness?" } }] }], vercel: [],
  }, view_version: "v1",
});
function events(snapshot: Snapshot, interrupt = false) {
  const outcome = interrupt ? { type: "interrupt", interrupts: [{ id: "q001", reason: "input_required", toolCallId: "q001", message: "What is the plate thickness?" }] } : { type: "success" };
  const data = [
    { type: "RUN_STARTED", threadId: snapshot.run_id, runId: "wire-run" },
    { type: "MESSAGES_SNAPSHOT", messages: snapshot.messages.agui },
    { type: "STATE_SNAPSHOT", snapshot },
    { type: "RUN_FINISHED", threadId: snapshot.run_id, runId: "wire-run", outcome },
  ].map(event => `data: ${JSON.stringify(event)}\n\n`).join("");
  return new Response(data, { headers: { "Content-Type": "text/event-stream" } });
}
function done(): Snapshot {
  return { ...pending(), status: "completed", phase: "verified", question: null, view_version: "v2", outcome: { status: "completed", verification_scope: "execution_evidence", summary: "Host accepted deliverable", usage: {} }, verification: { passed: true, feedback: "fresh artifact" }, messages: { ...pending().messages, agui: [{ id: "result", role: "assistant", content: "Done." }], aui: [{ id: "result", role: "assistant", content: "Done." }] } };
}
describe("native runtime wiring (no browser, model or backend writes)", () => {
  it("hydrates interrupt metadata through a real history adapter, then native hook POSTs resume", async () => {
    const fetchMock = vi.fn().mockResolvedValue(events(done()));
    vi.stubGlobal("fetch", fetchMock);
    const refresh = vi.fn().mockResolvedValue(undefined);
    render(<AgUiArm snapshot={pending()} refresh={refresh}/>);
    fireEvent.change(screen.getByLabelText("Answer"), { target: { value: "1 mm" } });
    await waitFor(() => expect((screen.getByRole("button", { name: "Submit answer" }) as HTMLButtonElement).disabled).toBe(false));
    expect(fetchMock).not.toHaveBeenCalled();
    expect(screen.getByText("Waiting for answer")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Submit answer" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const [url, options] = fetchMock.mock.calls[0]!;
    expect(url).toBe("/api/agui/host-run-001");
    const body = JSON.parse(options.body);
    expect(body.threadId).toBe("host-run-001");
    expect(body.tools).toEqual([]);
    expect(body.resume).toEqual([{ interruptId: "q001", status: "resolved", payload: { answer: "1 mm", task_revision: 0 } }]);
    await waitFor(() => expect(refresh).toHaveBeenCalled());
  });

  it("starts a registered task with the native append action and receives a genuine interrupt", async () => {
    const fetchMock = vi.fn().mockResolvedValue(events(pending(), true));
    vi.stubGlobal("fetch", fetchMock);
    const snapshot = { ...pending(), status: "registered" as const, question: null, messages: { agui: [], aui: [], vercel: [] } };
    const refresh = vi.fn().mockResolvedValue(undefined);
    const view = render(<AgUiArm snapshot={snapshot} refresh={refresh}/>);
    await waitFor(() => expect((screen.getByRole("button", { name: "Start task" }) as HTMLButtonElement).disabled).toBe(false));
    expect(fetchMock).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Start task" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const body = JSON.parse(fetchMock.mock.calls[0]![1].body);
    expect(body.threadId).toBe(snapshot.run_id);
    expect(body.messages.at(-1)).toMatchObject({ role: "user", content: "Start task" });
    expect(body.tools).toEqual([]);
    await waitFor(() => expect(refresh).toHaveBeenCalled());
    view.rerender(<AgUiArm snapshot={{ ...pending(), view_version: "pending-v2" }} refresh={refresh}/>);
    fireEvent.change(screen.getByLabelText("Answer"), { target: { value: "1" } });
    await waitFor(() => expect((screen.getByRole("button", { name: "Submit answer" }) as HTMLButtonElement).disabled).toBe(false));
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("external answers use command UUID, question ID and revision through REST only", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(done())));
    vi.stubGlobal("fetch", fetchMock);
    const refresh = vi.fn().mockResolvedValue(undefined);
    render(<ExternalArm snapshot={{ ...pending(), arm: "external" }} refresh={refresh}/>);
    expect(screen.getByText("Tool · ask_user")).toBeTruthy();
    expect(screen.getByText("Waiting for answer")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Answer"), { target: { value: "1 mm" } });
    fireEvent.click(screen.getByRole("button", { name: "Submit answer" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(fetchMock.mock.calls[0]![0]).toBe("/api/runs/host-run-001/answer");
    expect(JSON.parse(fetchMock.mock.calls[0]![1].body)).toEqual({ command_id: expect.stringMatching(/^[0-9a-f-]{36}$/), question_id: "q001", task_revision: 0, answer: "1 mm" });
  });

  it.each(["agui", "external"] as const)("%s completed/failed render host verdict separately with no sending actions", async arm => {
    const fetchMock = vi.fn(); vi.stubGlobal("fetch", fetchMock);
    const Component = arm === "agui" ? AgUiArm : ExternalArm;
    const snapshot = { ...done(), arm, status: "failed" as const, verification: { passed: false, feedback: "missing artifact" } };
    render(<Component snapshot={snapshot} refresh={async () => {}}/>);
    await waitFor(() => expect(screen.getByTestId("host-status").textContent).toBe("failed"));
    expect(screen.getByTestId("host-verdict").textContent).toBe("Failed");
    expect(screen.getByTestId("host-scope").textContent).toBe("execution_evidence");
    expect(screen.queryByRole("button")).toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("can hydrate a host question even when no assistant message exists", () => {
    const snapshot = pending(); snapshot.messages.agui = [];
    const message = hostRepository(snapshot).messages[0]!.message;
    expect(message.status).toEqual({ type: "requires-action", reason: "interrupt" });
    expect(message.metadata.custom.agui).toMatchObject({ interrupts: [{ id: "q001" }] });
  });

  it("renders an answered ask_user tool with its result", () => {
    const snapshot = done();
    snapshot.messages.aui = [{ id: "answered", role: "assistant", content: [{ type: "tool-call", toolCallId: "q001", toolName: "ask_user", args: { question: "Thickness?" }, result: "1 mm" }] }];
    render(<ExternalArm snapshot={{ ...snapshot, arm: "external" }} refresh={async () => {}}/>);
    expect(screen.getByText("Answered")).toBeTruthy();
    expect(screen.queryByText("Waiting for answer")).toBeNull();
  });

  it.each(["agui", "external"] as const)("%s host event reconnect only reads snapshots, never dispatches a command", async arm => {
    const snapshot = { ...pending(), arm };
    const fetchMock = vi.fn(async (url: string) => new Response(JSON.stringify(url.includes("?arm=") ? { runs: [] } : snapshot)));
    vi.stubGlobal("fetch", fetchMock);
    const sources: FakeEvents[] = [];
    class FakeEvents extends EventTarget {
      onopen: (() => void) | null = null;
      onerror: (() => void) | null = null;
      close = vi.fn();
      constructor(public url: string) { super(); sources.push(this); }
    }
    vi.stubGlobal("EventSource", FakeEvents);
    const { result, unmount } = renderHook(() => useHost(arm, snapshot.run_id));
    await waitFor(() => expect(result.current.snapshot?.status).toBe("waiting_for_user"));
    act(() => { sources[0]!.onerror?.(); sources[0]!.onopen?.(); sources[0]!.dispatchEvent(new Event("sync")); sources[0]!.dispatchEvent(new Event("run.changed")); });
    await waitFor(() => expect(fetchMock.mock.calls.length).toBeGreaterThan(2));
    expect(fetchMock.mock.calls.every(call => (call as unknown as [string, RequestInit])[1].method === "GET")).toBe(true);
    expect(sources[0]!.url).toBe("/api/runs/host-run-001/events");
    unmount(); expect(sources[0]!.close).toHaveBeenCalledOnce();
  });
});
