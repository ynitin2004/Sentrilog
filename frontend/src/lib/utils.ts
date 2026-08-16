import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'

/** Merges Tailwind classes, resolving conflicts (later classes win) -- the
 * standard shadcn/ui pattern. Every component that accepts a `className`
 * prop should route it through this rather than string-concatenating. */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs))
}

export function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  })
}

export function formatDateTime(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  })
}

export function formatRiskScore(score: number | null): string {
  if (score === null) return '—'
  return score.toFixed(2)
}

/** Copies to the clipboard, falling back silently if the Clipboard API is
 * unavailable (e.g. non-HTTPS/non-localhost contexts) -- callers show their
 * own success/failure toast based on the returned boolean. */
export async function copyToClipboard(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    return false
  }
}
