import * as React from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useAuth } from '@/lib/auth-context'
import { createApiClient, type ApiClient } from '@/lib/api-client'
import type {
  ApiKey,
  ApiKeyCreateResponse,
  Case,
  CaseDetail,
  DashboardSummary,
  ReviewDecision,
  Reviewer,
  ReviewerRole,
  ReviewQueueCase,
  Webhook,
  WebhookDelivery,
} from '@/types/api'

/** null only when there's no session -- every real caller here lives behind AuthGuard, so this
 * is effectively always non-null in practice; queries key off `client !== null` via `enabled`
 * rather than the hook throwing, since throwing inside a hook body is a render-time crash. */
function useApiClient(): ApiClient | null {
  const { session } = useAuth()
  return React.useMemo(() => (session ? createApiClient(session) : null), [session])
}

export function useReviewQueue() {
  const client = useApiClient()
  const queryClient = useQueryClient()
  const queryKey = ['review-queue']

  const query = useQuery({
    queryKey,
    queryFn: () => client!.get<ReviewQueueCase[]>('/review/cases'),
    enabled: client !== null,
  })

  const claimMutation = useMutation({
    mutationFn: (caseId: string) => client!.post<ReviewQueueCase>(`/review/cases/${caseId}/claim`),
    // Optimistic: the reviewer sees "claimed" immediately, not after a round trip -- claiming
    // is advisory anyway (see PLAN.md), so there's nothing to lose by showing it eagerly.
    onMutate: async (caseId) => {
      await queryClient.cancelQueries({ queryKey })
      const previous = queryClient.getQueryData<ReviewQueueCase[]>(queryKey)
      queryClient.setQueryData<ReviewQueueCase[]>(queryKey, (prev) =>
        prev?.map((c) =>
          c.case_id === caseId
            ? { ...c, claimed_by_reviewer_id: 'you', claimed_at: new Date().toISOString() }
            : c,
        ),
      )
      return { previous }
    },
    onError: (_err, _caseId, context) => {
      if (context?.previous) queryClient.setQueryData(queryKey, context.previous)
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey }),
  })

  const decideMutation = useMutation({
    mutationFn: ({ caseId, decision, justification }: DecideArgs) =>
      client!.post(`/review/cases/${caseId}/decision`, { decision, justification }),
    // Escalation re-parks the case (stays in the queue, mirrors KycCaseWorkflow's own
    // signal-handling semantics); approve/reject remove it from the list optimistically, rolled
    // back if the request actually fails.
    onMutate: async ({ caseId, decision }) => {
      await queryClient.cancelQueries({ queryKey })
      const previous = queryClient.getQueryData<ReviewQueueCase[]>(queryKey)
      if (decision !== 'escalated') {
        queryClient.setQueryData<ReviewQueueCase[]>(queryKey, (prev) =>
          prev?.filter((c) => c.case_id !== caseId),
        )
      }
      return { previous }
    },
    onError: (_err, _vars, context) => {
      if (context?.previous) queryClient.setQueryData(queryKey, context.previous)
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey }),
  })

  return {
    data: query.data ?? [],
    isLoading: query.isLoading,
    // mutateAsync, not mutate -- claiming is optimistic in the cache (see onMutate above), but
    // the caller still needs to know if it actually failed (someone else claimed or decided the
    // case first) so it can show that honestly instead of a success toast for a claim that
    // silently rolled back.
    claim: (caseId: string) => claimMutation.mutateAsync(caseId),
    decide: (caseId: string, decision: ReviewDecision, justification: string) =>
      decideMutation.mutateAsync({ caseId, decision, justification }),
  }
}

interface DecideArgs {
  caseId: string
  decision: ReviewDecision
  justification: string
}

export function useCaseDetail(caseId: string | undefined) {
  const client = useApiClient()
  const query = useQuery({
    queryKey: ['review-case-detail', caseId],
    queryFn: () => client!.get<CaseDetail>(`/review/cases/${caseId}`),
    enabled: client !== null && caseId !== undefined,
  })
  return { data: query.data ?? null, isLoading: query.isLoading }
}

export function useCases() {
  const client = useApiClient()
  const query = useQuery({
    queryKey: ['cases'],
    queryFn: () => client!.get<Case[]>('/cases'),
    enabled: client !== null,
  })
  return { data: query.data ?? [], isLoading: query.isLoading }
}

export function useDashboardSummary() {
  const client = useApiClient()
  const query = useQuery({
    queryKey: ['dashboard-summary'],
    queryFn: () => client!.get<DashboardSummary>('/dashboard/summary'),
    enabled: client !== null,
  })
  return {
    data: query.data ?? {
      status_counts: { pending: 0, processing: 0, needs_review: 0, approved: 0, rejected: 0 },
      cases_last_30_days: [],
      recent_activity: [],
    },
    isLoading: query.isLoading,
  }
}

export function useApiKeys() {
  const client = useApiClient()
  const queryClient = useQueryClient()
  const queryKey = ['api-keys']

  const query = useQuery({
    queryKey,
    queryFn: () => client!.get<ApiKey[]>('/api-keys'),
    enabled: client !== null,
  })

  const createMutation = useMutation({
    mutationFn: (name: string) => client!.post<ApiKeyCreateResponse>('/api-keys', { name }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey }),
  })

  const revokeMutation = useMutation({
    mutationFn: (id: string) => client!.post<ApiKey>(`/api-keys/${id}/revoke`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey }),
  })

  return {
    data: query.data ?? [],
    isLoading: query.isLoading,
    create: (name: string) => createMutation.mutateAsync(name),
    revoke: (id: string) => revokeMutation.mutate(id),
  }
}

export function useWebhooks() {
  const client = useApiClient()
  const queryClient = useQueryClient()
  const queryKey = ['webhooks']

  const query = useQuery({
    queryKey,
    queryFn: () => client!.get<Webhook[]>('/webhooks'),
    enabled: client !== null,
  })

  const createMutation = useMutation({
    mutationFn: (url: string) => client!.post('/webhooks', { url }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey }),
  })

  const disableMutation = useMutation({
    mutationFn: (id: string) => client!.post<Webhook>(`/webhooks/${id}/disable`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey }),
  })

  return {
    data: query.data ?? [],
    isLoading: query.isLoading,
    create: async (url: string) => {
      await createMutation.mutateAsync(url)
    },
    disable: (id: string) => disableMutation.mutate(id),
  }
}

export function useWebhookDeliveries(webhookId: string | undefined) {
  const client = useApiClient()
  const query = useQuery({
    queryKey: ['webhook-deliveries', webhookId],
    queryFn: () => client!.get<WebhookDelivery[]>(`/webhooks/${webhookId}/deliveries`),
    enabled: client !== null && webhookId !== undefined,
  })
  return { data: query.data ?? [], isLoading: query.isLoading }
}

export function useReviewers() {
  const client = useApiClient()
  const queryClient = useQueryClient()
  const queryKey = ['reviewers']

  const query = useQuery({
    queryKey,
    queryFn: () => client!.get<Reviewer[]>('/reviewers'),
    enabled: client !== null,
  })

  const createMutation = useMutation({
    mutationFn: ({ email, role }: { email: string; role: ReviewerRole }) =>
      client!.post('/reviewers', { email, role }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey }),
  })

  const revokeMutation = useMutation({
    mutationFn: (id: string) => client!.post(`/reviewers/${id}/revoke`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey }),
  })

  return {
    data: query.data ?? [],
    isLoading: query.isLoading,
    create: async (email: string, role: ReviewerRole) => {
      await createMutation.mutateAsync({ email, role })
    },
    revoke: (id: string) => revokeMutation.mutate(id),
  }
}
