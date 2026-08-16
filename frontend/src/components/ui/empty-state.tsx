import type { LucideIcon } from 'lucide-react'
import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'

export interface EmptyStateProps {
  icon: LucideIcon
  title: string
  description?: string
  action?: ReactNode
  className?: string
}

export function EmptyState({ icon: Icon, title, description, action, className }: EmptyStateProps) {
  return (
    <div
      className={cn(
        'border-border flex flex-col items-center justify-center gap-3 rounded-lg border border-dashed px-6 py-16 text-center',
        className,
      )}
    >
      <div className="bg-surface-raised rounded-full p-3">
        <Icon className="text-text-subtle h-6 w-6" aria-hidden="true" />
      </div>
      <div className="space-y-1">
        <p className="text-text text-sm font-medium">{title}</p>
        {description && <p className="text-text-subtle text-sm">{description}</p>}
      </div>
      {action}
    </div>
  )
}
