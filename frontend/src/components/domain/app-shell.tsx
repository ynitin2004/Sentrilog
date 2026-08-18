import * as React from 'react'
import { NavLink } from 'react-router-dom'
import {
  Inbox,
  FolderOpen,
  KeyRound,
  Webhook,
  Users,
  LayoutDashboard,
  LogOut,
  Menu,
  X,
  ShieldCheck,
  Wifi,
  WifiOff,
} from 'lucide-react'
import { useAuth, type Persona } from '@/lib/auth-context'
import { useCaseEventsStream, type SseStatus } from '@/hooks/use-case-events'
import { cn } from '@/lib/utils'

interface NavItem {
  to: string
  label: string
  icon: React.ComponentType<{ className?: string }>
}

const REVIEWER_NAV: NavItem[] = [{ to: '/reviewer/queue', label: 'Queue', icon: Inbox }]

const ADMIN_NAV: NavItem[] = [
  { to: '/admin/overview', label: 'Overview', icon: LayoutDashboard },
  { to: '/admin/cases', label: 'Cases', icon: FolderOpen },
  { to: '/admin/api-keys', label: 'API Keys', icon: KeyRound },
  { to: '/admin/webhooks', label: 'Webhooks', icon: Webhook },
  { to: '/admin/reviewers', label: 'Reviewers', icon: Users },
]

export function AppShell({ persona, children }: { persona: Persona; children: React.ReactNode }) {
  const { session, disconnect } = useAuth()
  const [mobileNavOpen, setMobileNavOpen] = React.useState(false)
  const items = persona === 'reviewer' ? REVIEWER_NAV : ADMIN_NAV
  const sseStatus = useCaseEventsStream()

  const nav = (
    <nav className="flex flex-1 flex-col gap-1 p-3" aria-label="Primary">
      {items.map((item) => (
        <NavLink
          key={item.to}
          to={item.to}
          onClick={() => setMobileNavOpen(false)}
          className={({ isActive }) =>
            cn(
              'flex items-center gap-2.5 rounded-md px-3 py-2 text-sm font-medium transition-colors',
              isActive
                ? 'bg-brand-bg text-brand-text'
                : 'text-text-muted hover:bg-surface-raised hover:text-text',
            )
          }
        >
          <item.icon className="h-4 w-4" />
          {item.label}
        </NavLink>
      ))}
    </nav>
  )

  return (
    <div className="flex min-h-screen">
      {/* Desktop sidebar */}
      <aside className="border-border bg-surface hidden w-56 shrink-0 border-r md:flex md:flex-col">
        <div className="border-border flex items-center gap-2 border-b px-4 py-4">
          <ShieldCheck className="text-brand h-5 w-5" aria-hidden="true" />
          <span className="text-text font-semibold">Sentrilog</span>
        </div>
        {nav}
        <SessionFooter
          apiBase={session?.apiBase}
          persona={persona}
          sseStatus={sseStatus}
          onDisconnect={disconnect}
        />
      </aside>

      {/* Mobile sidebar (overlay) */}
      {mobileNavOpen && (
        <div className="fixed inset-0 z-40 md:hidden">
          <div className="absolute inset-0 bg-black/50" onClick={() => setMobileNavOpen(false)} />
          <aside className="border-border bg-surface absolute inset-y-0 left-0 flex w-64 flex-col border-r">
            <div className="border-border flex items-center justify-between border-b px-4 py-4">
              <div className="flex items-center gap-2">
                <ShieldCheck className="text-brand h-5 w-5" aria-hidden="true" />
                <span className="text-text font-semibold">Sentrilog</span>
              </div>
              <button
                type="button"
                onClick={() => setMobileNavOpen(false)}
                aria-label="Close navigation"
                className="text-text-subtle"
              >
                <X className="h-5 w-5" />
              </button>
            </div>
            {nav}
            <SessionFooter
          apiBase={session?.apiBase}
          persona={persona}
          sseStatus={sseStatus}
          onDisconnect={disconnect}
        />
          </aside>
        </div>
      )}

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="border-border bg-surface flex items-center gap-3 border-b px-4 py-3 md:hidden">
          <button
            type="button"
            onClick={() => setMobileNavOpen(true)}
            aria-label="Open navigation"
            className="text-text-subtle"
          >
            <Menu className="h-5 w-5" />
          </button>
          <span className="text-text font-semibold">Sentrilog</span>
        </header>
        <main className="flex-1 p-4 md:p-8">{children}</main>
      </div>
    </div>
  )
}

function SessionFooter({
  apiBase,
  persona,
  sseStatus,
  onDisconnect,
}: {
  apiBase: string | undefined
  persona: Persona
  sseStatus: SseStatus
  onDisconnect: () => void
}) {
  return (
    <div className="border-border border-t p-3">
      <p className="text-text-subtle truncate px-1 text-xs" title={apiBase}>
        {apiBase ?? 'Not connected'}
      </p>
      <div className="flex items-center justify-between px-1">
        <p className="text-text-subtle text-xs capitalize">{persona} console</p>
        <LiveIndicator status={sseStatus} />
      </div>
      <button
        type="button"
        onClick={onDisconnect}
        className="text-text-muted hover:bg-surface-raised hover:text-text mt-2 flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-sm"
      >
        <LogOut className="h-4 w-4" /> Disconnect
      </button>
    </div>
  )
}

const SSE_STATUS_LABEL: Record<SseStatus, string> = {
  connecting: 'Connecting...',
  open: 'Live',
  reconnecting: 'Reconnecting...',
}

/** Surfaces the real-time connection's state rather than hiding it -- a silently-stalled stream
 * would look identical to a working one otherwise, and "reconnecting" is exactly the state a
 * real network drop puts this in (see PLAN.md Phase 10's exit criteria). */
function LiveIndicator({ status }: { status: SseStatus }) {
  const label = SSE_STATUS_LABEL[status]
  return (
    <span
      className={cn(
        'flex items-center gap-1 text-xs',
        status === 'open' ? 'text-success' : 'text-text-subtle',
      )}
      title={label}
    >
      {status === 'reconnecting' ? (
        <WifiOff className="h-3 w-3" aria-hidden="true" />
      ) : (
        <Wifi className="h-3 w-3" aria-hidden="true" />
      )}
      <span className="sr-only">{label}</span>
    </span>
  )
}
