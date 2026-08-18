import type { ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { AuthProvider, SESSION_STORAGE_KEY } from '@/lib/auth-context'
import { useCaseEventsStream } from './use-case-events'

function seedSession() {
  localStorage.setItem(
    SESSION_STORAGE_KEY,
    JSON.stringify({ token: 't', persona: 'reviewer', apiBase: 'http://localhost:9999' }),
  )
}

/** A ReadableStream that yields the given already-encoded SSE frames, then -- by default --
 * hangs open rather than closing, the same way a real SSE connection stays open indefinitely
 * between events. Pass keepOpen: false for the one test that specifically wants to see the
 * hook react to the server actually closing the stream. */
function streamOf(
  frames: string[],
  { keepOpen = true }: { keepOpen?: boolean } = {},
): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder()
  let i = 0
  return new ReadableStream({
    pull(controller) {
      if (i < frames.length) {
        controller.enqueue(encoder.encode(frames[i]))
        i += 1
        return undefined
      }
      if (keepOpen) return new Promise<void>(() => {}) // never resolves: connection stays open
      controller.close()
      return undefined
    },
  })
}

function makeWrapper(queryClient: QueryClient) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return (
      <AuthProvider>
        <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
      </AuthProvider>
    )
  }
}

describe('useCaseEventsStream', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('never connects when there is no session', () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)

    renderHook(() => useCaseEventsStream(), { wrapper: makeWrapper(queryClient) })

    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('opens the stream with a Bearer auth header and invalidates matching query keys', async () => {
    seedSession()
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries')

    const frame =
      'event: case_status_changed\n' +
      'data: {"tenant_id":"t1","case_id":"c1","status":"approved","decision":"approved"}\n\n'
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, body: streamOf([frame]) })
    vi.stubGlobal('fetch', fetchMock)

    const { result } = renderHook(() => useCaseEventsStream(), {
      wrapper: makeWrapper(queryClient),
    })

    await waitFor(() => expect(result.current).toBe('open'))
    expect(fetchMock).toHaveBeenCalledWith(
      'http://localhost:9999/events/stream',
      expect.objectContaining({ headers: { Authorization: 'Bearer t' } }),
    )

    await waitFor(() =>
      expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['review-case-detail', 'c1'] }),
    )
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['review-queue'] })
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['cases'] })
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['dashboard-summary'] })
  })

  it('ignores keepalive comments and the initial retry line without invalidating anything', async () => {
    seedSession()
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries')

    const fetchMock = vi
      .fn()
      .mockResolvedValue({ ok: true, body: streamOf(['retry: 3000\n\n', ': keep-alive\n\n']) })
    vi.stubGlobal('fetch', fetchMock)

    const { result } = renderHook(() => useCaseEventsStream(), {
      wrapper: makeWrapper(queryClient),
    })

    await waitFor(() => expect(result.current).toBe('open'))
    expect(invalidateSpy).not.toHaveBeenCalled()
  })

  it('reconnects with backoff after a dropped connection', async () => {
    seedSession()
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })

    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(new Error('simulated network drop'))
      .mockResolvedValueOnce({ ok: true, body: streamOf([]) })
    vi.stubGlobal('fetch', fetchMock)

    const { result } = renderHook(() => useCaseEventsStream(), {
      wrapper: makeWrapper(queryClient),
    })

    await waitFor(() => expect(result.current).toBe('reconnecting'))
    await waitFor(() => expect(result.current).toBe('open'), { timeout: 5000 })
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })
})
