import type { LucideIcon } from 'lucide-react'
import { Card, CardContent } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { cn } from '@/lib/utils'

export interface StatCardProps {
  label: string
  value: string | number
  icon: LucideIcon
  isLoading?: boolean
  tone?: 'neutral' | 'brand' | 'warning' | 'danger'
}

const TONE_CLASS: Record<NonNullable<StatCardProps['tone']>, string> = {
  neutral: 'bg-surface-raised text-text-muted',
  brand: 'bg-brand-bg text-brand-text',
  warning: 'bg-status-needs-review-bg text-status-needs-review',
  danger: 'bg-status-rejected-bg text-status-rejected',
}

export function StatCard({
  label,
  value,
  icon: Icon,
  isLoading = false,
  tone = 'neutral',
}: StatCardProps) {
  return (
    <Card>
      <CardContent className="flex items-center gap-4">
        <div
          className={cn(
            'flex h-10 w-10 shrink-0 items-center justify-center rounded-lg',
            TONE_CLASS[tone],
          )}
        >
          <Icon className="h-5 w-5" aria-hidden="true" />
        </div>
        <div>
          <p className="text-text-subtle text-xs font-medium">{label}</p>
          {isLoading ? (
            <Skeleton className="mt-1 h-6 w-14" />
          ) : (
            <p className="text-text text-xl font-semibold">{value}</p>
          )}
        </div>
      </CardContent>
    </Card>
  )
}
