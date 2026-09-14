import { useCallback, useEffect, useRef, useState } from "react";
import { errorText, request, runPath, type Arm, type Snapshot, type RunItem } from "./api";

export function useHost(arm: Arm, runId?: string) {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [runs, setRuns] = useState<RunItem[]>([]);
  const [error, setError] = useState("");
  const [connected, setConnected] = useState(false);
  const sequence = useRef(0);
  const refresh = useCallback(async () => {
    const current = ++sequence.current;
    try {
      const [list, next] = await Promise.all([
        request<{ runs: RunItem[] }>(`/api/runs?arm=${arm}`),
        runId ? request<Snapshot>(runPath(runId)) : Promise.resolve(null),
      ]);
      if (sequence.current !== current) return;
      if (next && (next.run_id !== runId || next.arm !== arm)) throw new Error("Host returned a task from a different route.");
      setRuns(list.runs);
      setSnapshot(next);
      setError("");
    } catch (error) {
      if (sequence.current === current) setError(errorText(error));
    }
  }, [arm, runId]);

  useEffect(() => {
    void refresh();
    // Read-only fallback also refreshes the task list when no task is selected.
    const timer = window.setInterval(() => void refresh(), 4000);
    const events = runId ? new EventSource(`${runPath(runId)}/events`) : null;
    if (events) {
      events.onopen = () => { setConnected(true); void refresh(); };
      events.onerror = () => setConnected(false);
      events.addEventListener("sync", () => void refresh());
      events.addEventListener("run.changed", () => void refresh());
    }
    return () => { sequence.current++; window.clearInterval(timer); events?.close(); };
  }, [refresh, runId]);
  return { snapshot, runs, error, connected, refresh };
}
