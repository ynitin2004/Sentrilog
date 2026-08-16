import * as React from 'react'
import { cn } from '@/lib/utils'

export const Input = React.forwardRef<
  HTMLInputElement,
  React.InputHTMLAttributes<HTMLInputElement>
>(({ className, ...props }, ref) => (
  <input
    ref={ref}
    className={cn(
      'border-border bg-surface text-text placeholder:text-text-subtle h-9 w-full rounded-md border px-3 text-sm',
      'focus-visible:border-brand focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-[var(--color-brand)]',
      'disabled:cursor-not-allowed disabled:opacity-50',
      'aria-invalid:border-danger',
      className,
    )}
    {...props}
  />
))
Input.displayName = 'Input'
