import * as React from 'react'
import { cva, type VariantProps } from 'class-variance-authority'
import { cn } from '@/lib/utils'

const badgeVariants = cva(
  'inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium whitespace-nowrap',
  {
    variants: {
      variant: {
        neutral: 'bg-surface-raised text-text-muted',
        brand: 'bg-brand-bg text-brand-text',
        success: 'bg-status-approved-bg text-status-approved',
        warning: 'bg-status-needs-review-bg text-status-needs-review',
        danger: 'bg-status-rejected-bg text-status-rejected',
      },
    },
    defaultVariants: { variant: 'neutral' },
  },
)

export interface BadgeProps
  extends React.HTMLAttributes<HTMLSpanElement>, VariantProps<typeof badgeVariants> {}

export function Badge({ className, variant, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ variant }), className)} {...props} />
}
