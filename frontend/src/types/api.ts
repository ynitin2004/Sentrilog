/**
 * Friendly re-exports over the generated OpenAPI types (api-generated.ts, produced by
 * `npm run generate-types` from the backend's own /openapi.json -- one source of truth, no
 * hand-duplicated shapes drifting from the real Pydantic schemas the way Phase 8's
 * hand-written version of this file inevitably would have). This layer exists for two things
 * generation can't give us on its own:
 *
 * 1. Clean names -- `components["schemas"]["ApiKeyResponse"]` everywhere is unreadable.
 * 2. Narrowing a few fields OpenAPI can't express as tightly as the backend actually
 *    guarantees (e.g. `status_counts` comes out as `{ [key: string]: number }` because
 *    Pydantic's `dict[str, int]` return type doesn't carry literal key names into the schema --
 *    but /dashboard/summary always zero-fills every CaseStatus key, so callers shouldn't have
 *    to defend against a key being absent).
 */
import type { components } from './api-generated'

export type CaseStatus = 'pending' | 'processing' | 'needs_review' | 'approved' | 'rejected'
export type ReviewDecision = 'approved' | 'rejected' | 'escalated'
export type ReviewerRole = 'reviewer' | 'admin' | 'auditor'
export type WebhookDeliveryStatus = 'pending' | 'delivered' | 'failed'

type Schemas = components['schemas']

export type Case = Schemas['CaseResponse']
export type ReviewQueueCase = Schemas['ReviewQueueCase']
export type ReviewDecisionRequest = Schemas['ReviewDecisionRequest']
export type ReviewDecisionResponse = Schemas['ReviewDecisionResponse']

export type Extraction = Schemas['ExtractionDetail']
export type FaceMatch = Schemas['FaceMatchDetail']
export type SanctionsHit = Schemas['SanctionsHitDetail']
export type CaseDetail = Schemas['ReviewCaseDetailResponse']

export type Reviewer = Schemas['ReviewerResponse']
export type ReviewerCreateRequest = Schemas['ReviewerCreateRequest']
export type ReviewerCreateResponse = Schemas['ReviewerCreateResponse']

export type ApiKey = Schemas['ApiKeyResponse']
export type ApiKeyCreateRequest = Schemas['ApiKeyCreateRequest']
export type ApiKeyCreateResponse = Schemas['ApiKeyCreateResponse']

export type Webhook = Schemas['WebhookResponse']
export type WebhookCreateRequest = Schemas['WebhookCreateRequest']
export type WebhookCreateResponse = Schemas['WebhookCreateResponse']
export type WebhookDelivery = Schemas['WebhookDeliveryResponse']

export type DashboardCaseVolumePoint = Schemas['DashboardCaseVolumePoint']
export type DashboardActivityItem = Schemas['DashboardActivityItem']

/** DashboardSummaryResponse, but with status_counts narrowed from the generated
 * `{ [key: string]: number }` to the Record every real response actually has every key of. */
export interface DashboardSummary extends Omit<
  Schemas['DashboardSummaryResponse'],
  'status_counts'
> {
  status_counts: Record<CaseStatus, number>
}

export interface ApiError {
  detail: string
}
