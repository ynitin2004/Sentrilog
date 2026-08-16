import { beforeEach, describe, expect, it } from 'vitest'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { render, screen } from '@testing-library/react'
import { AuthProvider, SESSION_STORAGE_KEY, type Session } from '@/lib/auth-context'
import { AuthGuard } from './auth-guard'

function ProtectedContent() {
  return <p>secret reviewer content</p>
}

function ConnectStub() {
  return <p>connect screen</p>
}

/** Seeds localStorage directly, the same way AuthProvider persists a session, so the session
 * is present on the guarded route's very first render -- avoids racing an effect-driven
 * connect() call (which requires an extra render cycle) against AuthGuard's own initial
 * evaluation. */
function seedSession(session: Session) {
  localStorage.setItem(SESSION_STORAGE_KEY, JSON.stringify(session))
}

function renderGuarded(initialEntries: string[]) {
  return render(
    <MemoryRouter initialEntries={initialEntries}>
      <AuthProvider>
        <Routes>
          <Route path="/connect" element={<ConnectStub />} />
          <Route
            path="/reviewer/queue"
            element={
              <AuthGuard persona="reviewer">
                <ProtectedContent />
              </AuthGuard>
            }
          />
        </Routes>
      </AuthProvider>
    </MemoryRouter>,
  )
}

describe('AuthGuard', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('redirects to /connect when there is no session', () => {
    renderGuarded(['/reviewer/queue'])
    expect(screen.getByText('connect screen')).toBeInTheDocument()
    expect(screen.queryByText('secret reviewer content')).not.toBeInTheDocument()
  })

  it('redirects to /connect when the session is for the wrong persona', () => {
    seedSession({ token: 't', persona: 'admin', apiBase: 'http://localhost:8000' })
    renderGuarded(['/reviewer/queue'])
    expect(screen.getByText('connect screen')).toBeInTheDocument()
  })

  it('renders the protected content when a matching-persona session exists', () => {
    seedSession({ token: 't', persona: 'reviewer', apiBase: 'http://localhost:8000' })
    renderGuarded(['/reviewer/queue'])
    expect(screen.getByText('secret reviewer content')).toBeInTheDocument()
  })
})
