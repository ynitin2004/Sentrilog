import type { ReactElement, ReactNode } from 'react'
import { render } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { AuthProvider } from '@/lib/auth-context'
import { ToastProvider } from '@/components/ui/toast'

function AllProviders({ children }: { children: ReactNode }) {
  return (
    <MemoryRouter>
      <AuthProvider>
        <ToastProvider>{children}</ToastProvider>
      </AuthProvider>
    </MemoryRouter>
  )
}

/** Wraps every render in the same providers the real app tree has (router, auth session,
 * toasts) -- components that call useAuth()/useToast()/useNavigate() would otherwise throw
 * immediately outside their real context. */
export function renderWithProviders(ui: ReactElement) {
  return render(ui, { wrapper: AllProviders })
}

export * from '@testing-library/react'
