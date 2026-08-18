import { afterEach, beforeEach, describe, it, vi } from 'vitest'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { SESSION_STORAGE_KEY } from '@/lib/auth-context'
import { renderWithProviders } from '@/test/test-utils'
import { expectNoA11yViolations } from '@/test/a11y'
import { AppShell } from './app-shell'

function seedSession() {
  localStorage.setItem(
    SESSION_STORAGE_KEY,
    JSON.stringify({ token: 't', persona: 'reviewer', apiBase: 'http://localhost:9999' }),
  )
}

describe('AppShell', () => {
  beforeEach(() => {
    localStorage.clear()
    // AppShell mounts useCaseEventsStream, which calls fetch on mount -- irrelevant to this
    // a11y check, so a never-resolving fetch keeps it out of the way without an unhandled
    // real network call.
    vi.stubGlobal(
      'fetch',
      vi.fn(() => new Promise(() => {})),
    )
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('has no accessibility violations, including the live-connection indicator', async () => {
    seedSession()
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })

    // renderWithProviders already wraps in MemoryRouter/AuthProvider/ToastProvider; nest a
    // QueryClientProvider inside that since AppShell's SSE hook needs useQueryClient().
    const { container } = renderWithProviders(
      <QueryClientProvider client={queryClient}>
        <AppShell persona="reviewer">
          <p>page content</p>
        </AppShell>
      </QueryClientProvider>,
    )
    await expectNoA11yViolations(container)
  })
})
