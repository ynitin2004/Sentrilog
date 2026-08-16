import * as React from 'react'
import { cn } from '@/lib/utils'

export const Textarea = React.forwardRef<
  HTMLTextAreaElement,
  React.TextareaHTMLAttributes<HTMLTextAreaElement>
>(({ className, ...props }, ref) => (
  <textarea
    ref={ref}
    className={cn(
      'border-border bg-surface text-text placeholder:text-text-subtle min-h-20 w-full rounded-md border px-3 py-2 text-sm',
      'focus-visible:border-brand focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-[var(--color-brand)]',
      'disabled:cursor-not-allowed disabled:opacity-50',
      'aria-invalid:border-danger',
      className,
    )}
    {...props}
  />
))
Textarea.displayName = 'Textarea'
