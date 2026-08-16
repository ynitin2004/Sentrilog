import type { CaseStatus, ReviewDecision, WebhookDeliveryStatus } from '@/types/api'
import { cn } from '@/lib/utils'

const CASE_STATUS_LABEL: Record<CaseStatus, string> = {
  pending: 'Pending',
  processing: 'Processing',
  needs_review: 'Needs Review',
  approved: 'Approved',
  rejected: 'Rejected',
}

// One canonical status -> color mapping (see index.css's --color-status-* tokens), reused by
// every screen that renders a case status -- the queue table, the cases list, the dashboard
// chart, and the case detail header never disagree about what color "needs_review" is.
const CASE_STATUS_CLASS: Record<CaseStatus, string> = {
  pending: 'bg-status-pending-bg text-status-pending',
  processing: 'bg-status-processing-bg text-status-processing',
  needs_review: 'bg-status-needs-review-bg text-status-needs-review',
  approved: 'bg-status-approved-bg text-status-approved',
  rejected: 'bg-status-rejected-bg text-status-rejected',
}

export function StatusBadge({ status, className }: { status: CaseStatus; className?: string }) {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium whitespace-nowrap',
        CASE_STATUS_CLASS[status],
        className,
      )}
    >
      <span className="h-1.5 w-1.5 rounded-full bg-current" aria-hidden="true" />
      {CASE_STATUS_LABEL[status]}
    </span>
  )
}

const DECISION_CLASS: Record<ReviewDecision, string> = {
  approved: 'bg-status-approved-bg text-status-approved',
  rejected: 'bg-status-rejected-bg text-status-rejected',
  escalated: 'bg-status-escalated-bg text-status-escalated',
}

const DECISION_LABEL: Record<ReviewDecision, string> = {
  approved: 'Approved',
  rejected: 'Rejected',
  escalated: 'Escalated',
}

export function DecisionBadge({ decision }: { decision: ReviewDecision }) {
  return (
    <span
      className={cn(
        'inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium',
        DECISION_CLASS[decision],
      )}
    >
      {DECISION_LABEL[decision]}
    </span>
  )
}

const DELIVERY_STATUS_CLASS: Record<WebhookDeliveryStatus, string> = {
  pending: 'bg-status-pending-bg text-status-pending',
  delivered: 'bg-status-approved-bg text-status-approved',
  failed: 'bg-status-rejected-bg text-status-rejected',
}

export function DeliveryStatusBadge({ status }: { status: WebhookDeliveryStatus }) {
  return (
    <span
      className={cn(
        'inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium capitalize',
        DELIVERY_STATUS_CLASS[status],
      )}
    >
      {status}
    </span>
  )
}
