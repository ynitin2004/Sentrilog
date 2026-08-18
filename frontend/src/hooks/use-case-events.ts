import * as React from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useAuth } from '@/lib/auth-context'

export type SseStatus = 'connecting' | 'open' | 'reconnecting'

const INITIAL_BACKOFF_MS = 1000
const MAX_BACKOFF_MS = 30000

interface CaseEvent {
  tenant_id: string
  case_id: string
  status: string
  decision: string | null
}

function parseFrame(frame: string): { eventType: string; data: string } {
  let eventType = 'message'
  let data = ''
  for (const line of frame.split('\n')) {
    if (line.startsWith('event:')) eventType = line.slice(6).trim()
    else if (line.startsWith('data:')) data += line.slice(5).trim()
  }
  return { eventType, data }
}

/** Consumes GET /events/stream via fetch + a manually-parsed ReadableStream, not native
 * EventSource -- EventSource cannot send an Authorization header, and this project's auth is a
 * Bearer token, not a cookie (see PLAN.md Phase 10). Invalidates the same query keys every
 * other hook in use-api.ts reads from, so a case claimed or decided anywhere shows up live
 * everywhere else without polling. Reconnects with exponential backoff (1s, 2s, 4s, ... capped
 * at 30s) on any drop -- a real network interruption is exactly what this needs to survive,
 * not just the happy path of a connection that never fails. */
export function useCaseEventsStream(): SseStatus {
  const { session } = useAuth()
  const queryClient = useQueryClient()
  const [status, setStatus] = React.useState<SseStatus>('connecting')

  React.useEffect(() => {
    if (!session) return undefined
    const activeSession = session // narrows for the closures below, which TS won't do on its own

    let cancelled = false
    let attempt = 0
    const controller = new AbortController()

    function handleFrame(frame: string): void {
      const { eventType, data } = parseFrame(frame)
      if (eventType !== 'case_status_changed' || !data) return
      let event: CaseEvent
      try {
        event = JSON.parse(data) as CaseEvent
      } catch {
        return // malformed frame -- ignore rather than crash the stream over one bad payload
      }
      void queryClient.invalidateQueries({ queryKey: ['review-queue'] })
      void queryClient.invalidateQueries({ queryKey: ['cases'] })
      void queryClient.invalidateQueries({ queryKey: ['dashboard-summary'] })
      void queryClient.invalidateQueries({ queryKey: ['review-case-detail', event.case_id] })
    }

    async function connectOnce(): Promise<void> {
      setStatus(attempt === 0 ? 'connecting' : 'reconnecting')
      try {
        const response = await fetch(`${activeSession.apiBase}/events/stream`, {
          headers: { Authorization: `Bearer ${activeSession.token}` },
          signal: controller.signal,
        })
        if (!response.ok || !response.body) {
          throw new Error(`SSE connect failed: ${response.status}`)
        }
        setStatus('open')
        attempt = 0

        const reader = response.body.getReader()
        const decoder = new TextDecoder()
        let buffer = ''

        while (!cancelled) {
          const { value, done } = await reader.read()
          if (done) break
          buffer += decoder.decode(value, { stream: true })
          const frames = buffer.split('\n\n')
          buffer = frames.pop() ?? ''
          for (const frame of frames) handleFrame(frame)
        }
      } catch {
        // Falls through to the reconnect scheduling below regardless of whether this was a
        // thrown network error or a clean server-side stream close (both need a reconnect).
      }

      if (cancelled) return
      attempt += 1
      const delay = Math.min(INITIAL_BACKOFF_MS * 2 ** (attempt - 1), MAX_BACKOFF_MS)
      setStatus('reconnecting')
      await new Promise((resolve) => setTimeout(resolve, delay))
      if (!cancelled) void connectOnce()
    }

    void connectOnce()

    return () => {
      cancelled = true
      controller.abort()
    }
  }, [session, queryClient])

  return status
}
