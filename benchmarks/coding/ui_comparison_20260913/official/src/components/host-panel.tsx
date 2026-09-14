import { useRef, useState, type SyntheticEvent } from 'react'
import { Button } from './ui/button'
import { Textarea } from './ui/textarea'
import { request, runPath, type HostRun } from '@/host-api'

export function HostPanel({
  snapshot,
  runId,
  refresh,
  loadError,
  connection,
}: {
  snapshot: HostRun | null
  runId: string | null
  refresh: () => Promise<void>
  loadError: string
  connection: string
}) {
  const [goal, setGoal] = useState('')
  const [answer, setAnswer] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const submitting = useRef(false)
  const command = useRef<{ key: string; id: string } | null>(null)
  const question = snapshot?.question

  async function submit(kind: 'create' | 'start' | 'answer', event?: SyntheticEvent) {
    event?.preventDefault()
    if (submitting.current) return
    if (kind === 'create' && !goal.trim()) return
    if (kind === 'start' && snapshot?.status !== 'registered') return
    if (kind === 'answer' && (!question?.pending || !answer.trim() || snapshot?.status !== 'waiting_for_user')) return
    submitting.current = true
    setBusy(true)
    setError('')
    try {
      if (kind === 'create') {
        const created = await request<HostRun>('/api/runs', { arm: 'official', goal: goal.trim() })
        if (created.arm !== 'official' || !created.run_id) throw new Error('Host returned an invalid task binding')
        window.location.assign(runPath(created.run_id))
        return
      }
      if (!snapshot) return
      const payload =
        kind === 'answer'
          ? { question_id: question!.question_id, task_revision: snapshot.task_revision, answer: answer.trim() }
          : {}
      const key = JSON.stringify([snapshot.run_id, kind, payload])
      if (command.current?.key !== key) command.current = { key, id: crypto.randomUUID() }
      await request<HostRun>(`/api/runs/${encodeURIComponent(snapshot.run_id)}/${kind}`, {
        command_id: command.current.id,
        ...payload,
      })
      // Re-fetch canonical history; POST responses and client drafts never become model messages.
      await refresh()
      command.current = null
      if (kind === 'answer') setAnswer('')
    } catch (reason) {
      setError(
        `${reason instanceof Error ? reason.message : String(reason)}. The command may have reached the host; check its current state before submitting again.`,
      )
      await refresh()
    } finally {
      submitting.current = false
      setBusy(false)
    }
  }

  return (
    <section
      aria-label="Host task"
      className="m-3 shrink-0 max-h-[45vh] overflow-y-auto rounded-lg border bg-muted/30 p-4 space-y-3 text-sm"
    >
      <h2 className="font-semibold">Host task</h2>
      <p className="text-xs text-muted-foreground">{connection}</p>
      {(error || loadError) && (
        <p role="alert" className="text-destructive whitespace-pre-wrap">
          {error || loadError}
        </p>
      )}
      {!runId && (
        <form
          onSubmit={(event) => {
            void submit('create', event)
          }}
          className="space-y-2"
        >
          <label htmlFor="host-goal">Goal</label>
          <Textarea
            id="host-goal"
            value={goal}
            onChange={(event) => {
              setGoal(event.target.value)
            }}
            disabled={busy}
          />
          <Button type="submit" disabled={busy || !goal.trim()}>
            Create task
          </Button>
        </form>
      )}
      {runId && !snapshot && <p>Loading task {runId}…</p>}
      {snapshot && (
        <>
          <p className="break-all">
            <strong>Task:</strong> {snapshot.run_id}
          </p>
          <p className="whitespace-pre-wrap">
            <strong>Goal:</strong> {snapshot.goal}
          </p>
          <p>
            <strong>Status:</strong> <span data-testid="host-status">{snapshot.status}</span> · <strong>Phase:</strong>{' '}
            {snapshot.phase} · <strong>Revision:</strong> {snapshot.task_revision}
          </p>
          {snapshot.status === 'registered' && (
            <Button
              disabled={busy || Boolean(loadError)}
              onClick={() => {
                void submit('start')
              }}
            >
              Start task
            </Button>
          )}
          {question && (
            <div className="rounded-md border p-3 space-y-2">
              <p>
                <strong>{question.pending ? 'Pending question' : 'Answered question'}:</strong> {question.question}
              </p>
              <p className="text-xs text-muted-foreground">Question ID: {question.question_id}</p>
              {question.answer !== null && (
                <p className="whitespace-pre-wrap">
                  <strong>Recorded answer:</strong> {question.answer}
                </p>
              )}
              {question.pending && snapshot.status === 'waiting_for_user' && (
                <form
                  onSubmit={(event) => {
                    void submit('answer', event)
                  }}
                  className="space-y-2"
                >
                  <label htmlFor="host-answer">Answer</label>
                  <Textarea
                    id="host-answer"
                    value={answer}
                    onChange={(event) => {
                      setAnswer(event.target.value)
                    }}
                    disabled={busy}
                  />
                  <Button type="submit" disabled={busy || !answer.trim() || Boolean(loadError)}>
                    Submit answer
                  </Button>
                </form>
              )}
            </div>
          )}
          <div className="border-t pt-2 space-y-1" aria-label="Host result">
            <p>
              <strong>Host verification scope:</strong>{' '}
              {snapshot.outcome ? (
                <span data-testid="host-scope">{snapshot.outcome.verification_scope}</span>
              ) : (
                'Not reported'
              )}
            </p>
            <p>
              <strong>Host verification result:</strong>{' '}
              {snapshot.verification === null ? (
                'Not reported'
              ) : (
                <span data-testid="host-verdict">{snapshot.verification.passed ? 'pass' : 'fail'}</span>
              )}
            </p>
            {snapshot.outcome && (
              <>
                <p>
                  <strong>Host outcome:</strong> {snapshot.outcome.status}
                </p>
                <p className="whitespace-pre-wrap">{snapshot.outcome.summary}</p>
                <details>
                  <summary>Host usage</summary>
                  <pre className="whitespace-pre-wrap break-all">
                    {JSON.stringify(snapshot.outcome.usage, null, 2)}
                  </pre>
                </details>
              </>
            )}
            {snapshot.verification && (
              <pre className="whitespace-pre-wrap break-all">
                {typeof snapshot.verification.feedback === 'string'
                  ? snapshot.verification.feedback
                  : JSON.stringify(snapshot.verification.feedback, null, 2)}
              </pre>
            )}
          </div>
        </>
      )}
    </section>
  )
}
