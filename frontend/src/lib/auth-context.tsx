import * as React from 'react'

export type Persona = 'reviewer' | 'admin'

export interface Session {
  token: string
  persona: Persona
  apiBase: string
}

interface AuthContextValue {
  session: Session | null
  connect: (session: Session) => void
  disconnect: () => void
}

const AuthContext = React.createContext<AuthContextValue | null>(null)

// Exported so tests can seed/inspect a session by writing directly to localStorage instead of
// racing AuthProvider's async connect() against a component under test's own initial render.
export const SESSION_STORAGE_KEY = 'sentrilog_session'

/** localStorage, not an httpOnly cookie -- the pragmatic Phase 8/9 choice, matching
 * webui/reviewer.html's existing pattern. Real tradeoff, documented rather than glossed over:
 * localStorage is readable by any script on the page (XSS risk), while an httpOnly cookie
 * can't be read by JS at all but needs backend session/cookie support that doesn't exist yet.
 * Revisit in Phase 10 if this frontend ever handles genuinely high-value sessions. */
function loadSession(): Session | null {
  try {
    const raw = localStorage.getItem(SESSION_STORAGE_KEY)
    return raw ? (JSON.parse(raw) as Session) : null
  } catch {
    return null
  }
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [session, setSession] = React.useState<Session | null>(loadSession)

  const connect = React.useCallback((next: Session) => {
    localStorage.setItem(SESSION_STORAGE_KEY, JSON.stringify(next))
    setSession(next)
  }, [])

  const disconnect = React.useCallback(() => {
    localStorage.removeItem(SESSION_STORAGE_KEY)
    setSession(null)
  }, [])

  return (
    <AuthContext.Provider value={{ session, connect, disconnect }}>{children}</AuthContext.Provider>
  )
}

export function useAuth(): AuthContextValue {
  const ctx = React.useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within an AuthProvider')
  return ctx
}
