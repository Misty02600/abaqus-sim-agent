import type { components } from "./schema";
export type Snapshot = components["schemas"]["RunSnapshot"];
export type View = components["schemas"]["CodingRunView"];
export type Message = components["schemas"]["MessageView"];
export type ToolSummary = components["schemas"]["ToolSummary"];
export type Artifact = components["schemas"]["FileView"];
export type ToolExecution = components["schemas"]["ToolExecution"];
let csrf = "";

export async function request<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(`/api/v1${path}`, {
    method: body === undefined ? "GET" : "POST",
    credentials: "same-origin",
    headers:
      body === undefined
        ? {}
        : { "Content-Type": "application/json", "X-CSRF-Token": csrf },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok)
    throw new Error(
      typeof data.message === "string"
        ? data.message
        : `请求失败（${response.status}）`,
    );
  return data as T;
}

export async function authenticate() {
  const token = new URLSearchParams(location.hash.slice(1)).get("session");
  if (token) history.replaceState(null, "", location.pathname);
  const info = await request<components["schemas"]["SessionInfo"]>(
    "/session",
    token ? { token } : undefined,
  );
  csrf = info.csrf_token;
}

export function runPath(id: string) {
  return `/runs/${encodeURIComponent(id)}`;
}
export function errorText(error: unknown) {
  return error instanceof Error ? error.message : "操作失败";
}
export function commandId(runId: string, action: string, payload: string = "") {
  const key = `abaqus-command:${runId}:${action}`;
  const raw = sessionStorage.getItem(key);
  let saved: { id: string; payload: string } | null = null;
  try {
    saved = raw ? JSON.parse(raw) : null;
  } catch {
    /* Old or invalid browser cache is not authoritative. */
  }
  const id = saved?.payload === payload ? saved.id : crypto.randomUUID();
  sessionStorage.setItem(key, JSON.stringify({ id, payload }));
  return id;
}
export function clearCommand(runId: string, action: string) {
  sessionStorage.removeItem(`abaqus-command:${runId}:${action}`);
}
