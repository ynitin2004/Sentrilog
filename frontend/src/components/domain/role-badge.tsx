import type { ReviewerRole } from '@/types/api'
import { cn } from '@/lib/utils'

const ROLE_CLASS: Record<ReviewerRole, string> = {
  reviewer: 'bg-brand-bg text-brand-text',
  admin: 'bg-status-escalated-bg text-status-escalated',
  auditor: 'bg-surface-raised text-text-muted',
}

export function RoleBadge({ role }: { role: ReviewerRole }) {
  return (
    <span
      className={cn(
        'inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium capitalize',
        ROLE_CLASS[role],
      )}
    >
      {role}
    </span>
  )
}
