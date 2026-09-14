import type { ThreadMessageLike } from "@assistant-ui/react";
import type { Message } from "@ag-ui/client";

export type Arm = "external" | "agui";
export type Snapshot = {
  run_id: string; arm: Arm; goal: string;
  status: "registered" | "running" | "waiting_for_user" | "completed" | "failed";
  task_revision: 0; phase: string;
  question: null | { question_id: string; question: string; pending: boolean; answer: string | null };
  outcome: null | { status: string; verification_scope: string; summary: unknown; usage: unknown };
  verification: null | { passed: boolean; feedback: unknown };
  messages: { vercel: unknown[]; aui: ThreadMessageLike[]; agui: Message[] };
  view_version: string;
};
export type RunItem = Pick<Snapshot, "run_id" | "goal" | "status">;
export const runPath = (id: string) => `/api/runs/${encodeURIComponent(id)}`;
export const errorText = (error: unknown) => error instanceof Error ? error.message : String(error);
export async function request<T>(path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  const response = await fetch(path, {
    method: body === undefined ? "GET" : "POST",
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: "no-store", signal,
  });
  if (!response.ok) throw new Error(`${response.status}: ${(await response.text()).slice(0, 600)}`);
  return response.json() as Promise<T>;
}
