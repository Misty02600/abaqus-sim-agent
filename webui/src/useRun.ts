import { useCallback, useEffect, useRef, useState } from "react";
import {
  ApiError,
  request,
  runPath,
  errorText,
  type Snapshot,
} from "./api/client";

export function useRun(id: string | null) {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [error, setError] = useState("");
  const [connected, setConnected] = useState(false);
  const [deleted, setDeleted] = useState(false);
  const generation = useRef(0);
  const inFlight = useRef<{
    id: string;
    epoch: number;
    again: boolean;
    promise: Promise<void>;
  } | null>(null);
  const selected = useRef(id);
  selected.current = id;
  const refresh = useCallback(function refresh(): Promise<void> {
    if (!id) return Promise.resolve();
    const epoch = generation.current;
    const pending = inFlight.current;
    if (pending?.id === id && pending.epoch === epoch) {
      pending.again = true;
      return pending.promise;
    }
    const current = () => id === selected.current && epoch === generation.current;
    const flight = { id, epoch, again: false, promise: Promise.resolve() };
    inFlight.current = flight;
    flight.promise = request<Snapshot>(runPath(id))
      .then(next => {
        if (current()) {
          setSnapshot(next);
          setError("");
        }
      })
      .catch(error => {
        if (current()) {
          setError(errorText(error));
          if (error instanceof ApiError && error.status === 410) setDeleted(true);
        }
      })
      .finally(() => {
        if (inFlight.current === flight) inFlight.current = null;
        if (flight.again && current()) void refresh();
      });
    return flight.promise;
  }, [id]);
  useEffect(() => {
    ++generation.current;
    setSnapshot(null);
    setError("");
    setConnected(false);
    setDeleted(false);
    if (!id) return;
    const events = new EventSource(`/api/v1${runPath(id)}/events`);
    const sync = () => {
      setConnected(true);
      void refresh();
    };
    events.addEventListener("sync", sync);
    events.addEventListener("run.changed", sync);
    events.onopen = () => setConnected(true);
    events.onerror = () => setConnected(false);
    void refresh();
    const visible = () => {
      if (document.visibilityState === "visible") void refresh();
    };
    document.addEventListener("visibilitychange", visible);
    return () => {
      ++generation.current;
      events.close();
      document.removeEventListener("visibilitychange", visible);
    };
  }, [id, refresh]);
  const toolRunning =
    snapshot?.tool_execution?.status === "starting" ||
    snapshot?.tool_execution?.status === "running";
  useEffect(() => {
    if (!id || !toolRunning) return;
    const timer = window.setInterval(() => void refresh(), 1000);
    return () => window.clearInterval(timer);
  }, [id, toolRunning, refresh]);
  return {
    snapshot: snapshot?.view.run_id === id ? snapshot : null,
    error,
    connected,
    refresh,
    deleted,
  };
}
