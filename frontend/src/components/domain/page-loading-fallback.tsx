import { Skeleton } from '@/components/ui/skeleton'

/** Suspense fallback for a lazily-loaded route chunk -- shown for the brief window between
 * navigating to a screen and its JS actually arriving (fast on a warm cache, more noticeable
 * on a cold load or slow connection, which is exactly when route-based code splitting matters
 * most). Deliberately chrome-free (no AppShell) since the chunk that renders AppShell itself
 * might be part of what's still loading. */
export function PageLoadingFallback() {
  return (
    <div
      role="status"
      aria-label="Loading page"
      className="flex min-h-screen items-center justify-center p-8"
    >
      <div className="w-full max-w-5xl space-y-4">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-4 w-72" />
        <Skeleton className="h-48 w-full" />
      </div>
    </div>
  )
}
