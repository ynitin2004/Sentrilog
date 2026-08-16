import * as React from 'react'
import {
  mockApiKeys,
  mockCaseDetails,
  mockCases,
  mockDashboardSummary,
  mockReviewers,
  mockReviewQueue,
  mockWebhookDeliveries,
  mockWebhooks,
} from '@/mocks/fixtures'
import type {
  ApiKey,
  ApiKeyCreateResponse,
  Case,
  CaseDetail,
  DashboardSummary,
  ReviewDecision,
  ReviewerRole,
  ReviewQueueCase,
  Webhook,
  WebhookDelivery,
} from '@/types/api'

/**
 * Every hook here is shaped exactly like its Phase 9 replacement will be (same name, same
 * { data, isLoading } / mutate-function shape as a TanStack Query useQuery/useMutation pair)
 * so screens never change when Phase 9 swaps the body of each hook for a real fetch -- only
 * this file does. The artificial latency is deliberate: it's what makes the loading-skeleton
 * states in DataTable/StatCard actually exercised and visible on this phase's mock data,
 * rather than being dead code no mock ever triggers.
 */
const LATENCY_MS = 400

function useDelayed<T>(value: T, deps: React.DependencyList = []): { data: T; isLoading: boolean } {
  const [isLoading, setIsLoading] = React.useState(true)
  // deps is forwarded from the caller by design (e.g. "re-run when caseId changes") -- oxlint's
  // static analysis can't verify a dynamically-passed array, hence the disable below; the effect
  // body itself references nothing external that could actually go stale.
  // oxlint-disable-next-line react-hooks/exhaustive-deps
  React.useEffect(() => {
    setIsLoading(true)
    const timer = setTimeout(() => setIsLoading(false), LATENCY_MS)
    return () => clearTimeout(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)
  return { data: value, isLoading }
}

/**
 * A minimal external store (module-level state + subscribers, read via useSyncExternalStore) --
 * NOT per-component useState. Real bug this fixes, caught by a Playwright smoke test rather than
 * by inspection: with per-component useState, ReviewerQueuePage and ReviewerCaseDetailPage each
 * got their own independent copy of the queue, so approving a case on the detail page never
 * removed it from the queue list once you navigated back (each mount re-initialized from the
 * pristine fixture). This is the same role Phase 9's shared TanStack Query cache will play for
 * real -- one source of truth all components read and mutate together, not a copy each.
 */
function createMockStore<T>(initial: T) {
  let state = initial
  const listeners = new Set<() => void>()

  return {
    get: () => state,
    set: (updater: (prev: T) => T) => {
      state = updater(state)
      listeners.forEach((l) => l())
    },
    subscribe: (listener: () => void) => {
      listeners.add(listener)
      return () => listeners.delete(listener)
    },
  }
}

function useMockStore<T>(store: ReturnType<typeof createMockStore<T>>): T {
  return React.useSyncExternalStore(store.subscribe, store.get)
}

const reviewQueueStore = createMockStore<ReviewQueueCase[]>(mockReviewQueue)
const apiKeysStore = createMockStore<ApiKey[]>(mockApiKeys)
const webhooksStore = createMockStore<Webhook[]>(mockWebhooks)
const reviewersStore = createMockStore(mockReviewers)

export function useReviewQueue() {
  const cases = useMockStore(reviewQueueStore)
  const { isLoading } = useDelayed(null)

  const claim = React.useCallback((caseId: string) => {
    reviewQueueStore.set((prev) =>
      prev.map((c) =>
        c.case_id === caseId
          ? { ...c, claimed_by_reviewer_id: 'you', claimed_at: new Date().toISOString() }
          : c,
      ),
    )
  }, [])

  const decide = React.useCallback((caseId: string, decision: ReviewDecision) => {
    // Escalation re-parks the case (stays in the queue); approve/reject remove it -- mirrors
    // KycCaseWorkflow's actual signal-handling semantics (services/pipeline/workflows/kyc_case.py).
    if (decision === 'escalated') return
    reviewQueueStore.set((prev) => prev.filter((c) => c.case_id !== caseId))
  }, [])

  return { data: cases, isLoading, claim, decide }
}

export function useCaseDetail(caseId: string | undefined) {
  const detail = caseId ? (mockCaseDetails[caseId] ?? null) : null
  const { data, isLoading } = useDelayed(detail, [caseId])
  return { data, isLoading }
}

export function useCases() {
  return useDelayed(mockCases as Case[])
}

export function useDashboardSummary() {
  return useDelayed(mockDashboardSummary as DashboardSummary)
}

export function useApiKeys() {
  const keys = useMockStore(apiKeysStore)
  const { isLoading } = useDelayed(null)

  const create = React.useCallback(async (name: string): Promise<ApiKeyCreateResponse> => {
    await new Promise((r) => setTimeout(r, LATENCY_MS))
    const created: ApiKeyCreateResponse = {
      id: crypto.randomUUID(),
      name,
      created_at: new Date().toISOString(),
      revoked_at: null,
      raw_key: `sk_demo_${crypto.randomUUID().replace(/-/g, '')}`,
    }
    apiKeysStore.set((prev) => [created, ...prev])
    return created
  }, [])

  const revoke = React.useCallback((id: string) => {
    apiKeysStore.set((prev) =>
      prev.map((k) => (k.id === id ? { ...k, revoked_at: new Date().toISOString() } : k)),
    )
  }, [])

  return { data: keys, isLoading, create, revoke }
}

export function useWebhooks() {
  const webhooks = useMockStore(webhooksStore)
  const { isLoading } = useDelayed(null)

  const create = React.useCallback(async (url: string) => {
    await new Promise((r) => setTimeout(r, LATENCY_MS))
    webhooksStore.set((prev) => [
      { id: crypto.randomUUID(), url, created_at: new Date().toISOString(), disabled_at: null },
      ...prev,
    ])
  }, [])

  const disable = React.useCallback((id: string) => {
    webhooksStore.set((prev) =>
      prev.map((w) => (w.id === id ? { ...w, disabled_at: new Date().toISOString() } : w)),
    )
  }, [])

  return { data: webhooks, isLoading, create, disable }
}

export function useWebhookDeliveries(webhookId: string | undefined) {
  const deliveries = webhookId
    ? mockWebhookDeliveries.filter((d) => d.webhook_id === webhookId)
    : ([] as WebhookDelivery[])
  return useDelayed(deliveries, [webhookId])
}

export function useReviewers() {
  const reviewers = useMockStore(reviewersStore)
  const { isLoading } = useDelayed(null)

  const create = React.useCallback(async (email: string, role: ReviewerRole) => {
    await new Promise((r) => setTimeout(r, LATENCY_MS))
    reviewersStore.set((prev) => [
      {
        id: crypto.randomUUID(),
        email,
        role,
        created_at: new Date().toISOString(),
        revoked_at: null,
      },
      ...prev,
    ])
  }, [])

  const revoke = React.useCallback((id: string) => {
    reviewersStore.set((prev) =>
      prev.map((r) => (r.id === id ? { ...r, revoked_at: new Date().toISOString() } : r)),
    )
  }, [])

  return { data: reviewers, isLoading, create, revoke }
}

export type { CaseDetail }
