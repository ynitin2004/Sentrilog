import * as React from 'react'
import { MutationCache, QueryCache, QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '@/lib/auth-context'
import { UnauthorizedError } from '@/lib/api-client'

/** Created inside the component tree (not at module scope) specifically so its global error
 * handlers can reach useAuth().disconnect() and useNavigate() -- any query or mutation that
 * throws UnauthorizedError (a 401: the session token was revoked or is otherwise no longer
 * valid) clears the session and bounces to /connect from exactly one place, instead of every
 * screen needing to check for a 401 itself. */
export function QueryProvider({ children }: { children: React.ReactNode }) {
  const { disconnect } = useAuth()
  const navigate = useNavigate()

  const [queryClient] = React.useState(() => {
    const handleUnauthorized = (error: unknown) => {
      if (error instanceof UnauthorizedError) {
        disconnect()
        navigate('/connect')
      }
    }

    return new QueryClient({
      defaultOptions: {
        queries: {
          retry: (failureCount, error) => !(error instanceof UnauthorizedError) && failureCount < 2,
        },
      },
      queryCache: new QueryCache({ onError: handleUnauthorized }),
      mutationCache: new MutationCache({ onError: handleUnauthorized }),
    })
  })

  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
}
