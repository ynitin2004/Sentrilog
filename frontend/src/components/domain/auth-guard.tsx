import { Navigate, useLocation } from 'react-router-dom'
import { useAuth, type Persona } from '@/lib/auth-context'

/** Wraps every route under /reviewer/* and /admin/* -- no session, or a session for the wrong
 * persona (a reviewer token trying to view /admin, or vice versa), redirects to /connect rather
 * than rendering a broken screen against data that will 401. */
export function AuthGuard({ persona, children }: { persona: Persona; children: React.ReactNode }) {
  const { session } = useAuth()
  const location = useLocation()

  if (!session || session.persona !== persona) {
    return <Navigate to="/connect" state={{ from: location.pathname }} replace />
  }

  return <>{children}</>
}
