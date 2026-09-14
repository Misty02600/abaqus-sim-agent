import type { UIMessage } from 'ai'
import { useCallback, useEffect, useRef, useState } from 'react'

export interface HostRun {
  run_id: string
  arm: string
  goal: string
  status: 'registered' | 'running' | 'waiting_for_user' | 'completed' | 'failed'
  task_revision: number
  phase: string
  question: null | { question_id: string; question: string; pending: boolean; answer: string | null }
  outcome: null | { status: string; verification_scope: string; summary: string; usage: unknown }
  verification: null | { passed: boolean; feedback: unknown }
  messages: { vercel: UIMessage[]; aui: unknown[]; agui: unknown[] }
  view_version: string
}

export type RunEntry = Pick<HostRun, 'run_id' | 'goal' | 'status'>

export function runPath(id: string) {
  return `/official/${encodeURIComponent(id)}`
}

export async function request<T>(path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  const response = await fetch(path, {
    method: body === undefined ? 'GET' : 'POST',
    headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: 'no-store',
    signal,
  })
  if (!response.ok) throw new Error(`Host HTTP ${response.status}: ${(await response.text()).slice(0, 800)}`)
  return (await response.json()) as T
}

export function useHostRun(runId: string | null) {
  const [snapshot, setSnapshot] = useState<HostRun | null>(null)
  const [runs, setRuns] = useState<RunEntry[]>([])
  const [error, setError] = useState('')
  const [connection, setConnection] = useState('Connecting to host')
  const generation = useRef(0)
  const controller = useRef<AbortController | null>(null)

  const refresh = useCallback(async () => {
    const ticket = ++generation.current
    controller.current?.abort()
    const nextController = new AbortController()
    controller.current = nextController
    try {
      const [list, next] = await Promise.all([
        request<{ runs: RunEntry[] }>('/api/runs?arm=official', undefined, nextController.signal),
        runId ? request<HostRun>(`/api/runs/${encodeURIComponent(runId)}`, undefined, nextController.signal) : null,
      ])
      if (ticket !== generation.current) return
      if (next && (next.arm !== 'official' || next.run_id !== runId)) throw new Error('Host task binding mismatch')
      if (next && !Array.isArray(next.messages.vercel)) throw new Error('Host snapshot has no Vercel history')
      setRuns(list.runs)
      setSnapshot(next)
      setError('')
    } catch (reason) {
      if (ticket === generation.current && !nextController.signal.aborted) {
        setError(reason instanceof Error ? reason.message : String(reason))
      }
    }
  }, [runId])

  useEffect(() => {
    void refresh()
    const source = runId ? new EventSource(`/api/runs/${encodeURIComponent(runId)}/events`) : null
    const sync = () => {
      void refresh()
    }
    if (source) {
      source.addEventListener('sync', sync)
      source.addEventListener('run.changed', sync)
      source.onopen = () => {
        setConnection('Host events connected')
        void refresh()
      }
      source.onerror = () => {
        setConnection('Host events reconnecting; displayed snapshot may be stale')
      }
    } else {
      setConnection('Host task list')
    }
    window.addEventListener('focus', sync)
    return () => {
      ++generation.current
      controller.current?.abort()
      source?.close()
      window.removeEventListener('focus', sync)
    }
  }, [runId, refresh])

  return { snapshot, runs, error, connection, refresh }
}
