import * as React from 'react'
import * as ToastPrimitive from '@radix-ui/react-toast'
import { CheckCircle2, XCircle, X } from 'lucide-react'
import { cn } from '@/lib/utils'

export interface ToastMessage {
  id: string
  title: string
  description?: string
  variant?: 'success' | 'error'
}

interface ToastContextValue {
  toast: (message: Omit<ToastMessage, 'id'>) => void
}

const ToastContext = React.createContext<ToastContextValue | null>(null)

/** Central toast provider -- one instance at the app root (see main.tsx). Any component calls
 * useToast() rather than each owning its own notification state, so a claim/decision/create-key
 * action anywhere in the tree can surface feedback without prop-drilling a setter down to it. */
export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [messages, setMessages] = React.useState<ToastMessage[]>([])

  const toast = React.useCallback((message: Omit<ToastMessage, 'id'>) => {
    const id = crypto.randomUUID()
    setMessages((prev) => [...prev, { ...message, id }])
  }, [])

  const dismiss = (id: string) => setMessages((prev) => prev.filter((m) => m.id !== id))

  return (
    <ToastContext.Provider value={{ toast }}>
      <ToastPrimitive.Provider swipeDirection="right">
        {children}
        {messages.map((m) => (
          <ToastPrimitive.Root
            key={m.id}
            duration={5000}
            onOpenChange={(open) => !open && dismiss(m.id)}
            className={cn(
              'flex items-start gap-2 rounded-lg border p-3 shadow-lg',
              m.variant === 'error'
                ? 'border-status-rejected-bg bg-status-rejected-bg text-status-rejected'
                : 'border-status-approved-bg bg-status-approved-bg text-status-approved',
            )}
          >
            {m.variant === 'error' ? (
              <XCircle className="h-5 w-5 shrink-0" aria-hidden="true" />
            ) : (
              <CheckCircle2 className="h-5 w-5 shrink-0" aria-hidden="true" />
            )}
            <div className="flex-1 space-y-0.5">
              <ToastPrimitive.Title className="text-sm font-medium">{m.title}</ToastPrimitive.Title>
              {m.description && (
                <ToastPrimitive.Description className="text-xs opacity-90">
                  {m.description}
                </ToastPrimitive.Description>
              )}
            </div>
            <ToastPrimitive.Close aria-label="Dismiss" className="shrink-0">
              <X className="h-4 w-4" />
            </ToastPrimitive.Close>
          </ToastPrimitive.Root>
        ))}
        <ToastPrimitive.Viewport className="fixed right-4 bottom-4 z-[100] flex w-full max-w-sm flex-col gap-2 outline-none" />
      </ToastPrimitive.Provider>
    </ToastContext.Provider>
  )
}

export function useToast(): ToastContextValue {
  const ctx = React.useContext(ToastContext)
  if (!ctx) throw new Error('useToast must be used within a ToastProvider')
  return ctx
}
