/**
 * Types mirroring the backend's Pydantic schemas (services/intake/schemas.py) and, for
 * endpoints that don't exist yet (api-keys/webhooks/reviewers/cases-list/dashboard-summary --
 * all Phase 9 work), the underlying Postgres columns those endpoints will expose. Phase 9
 * replaces this whole file with types generated from the real /openapi.json -- hand-written
 * here only because Phase 8 has no backend to generate from yet.
 */

export type CaseStatus = 'pending' | 'processing' | 'needs_review' | 'approved' | 'rejected'

export type ReviewDecision = 'approved' | 'rejected' | 'escalated'

export type ReviewerRole = 'reviewer' | 'admin' | 'auditor'

export type WebhookDeliveryStatus = 'pending' | 'delivered' | 'failed'

/** Matches services/intake/schemas.py::CaseResponse */
export interface Case {
  case_id: string
  status: CaseStatus
  subject_name: string
  subject_dob: string | null
  decision: ReviewDecision | null
  created_at: string
  risk_score: number | null
}

/** Matches services/intake/schemas.py::ReviewQueueCase */
export interface ReviewQueueCase {
  case_id: string
  subject_name: string
  subject_dob: string | null
  risk_score: number | null
  created_at: string
  claimed_by_reviewer_id: string | null
  claimed_at: string | null
}

export interface Extraction {
  document_id: string
  method: 'mrz' | 'vlm'
  confidence: number
  valid: boolean
  full_name: string | null
  date_of_birth: string | null
  document_number: string | null
  nationality: string | null
  expiry_date: string | null
}

export interface FaceMatch {
  similarity_score: number | null
  reason: string | null
}

export interface SanctionsHit {
  list_source: string
  matched_name: string
  match_score: number
  method: 'vector' | 'phonetic'
}

/** The full case-detail view -- composes the case row with its extraction,
 * face match, and sanctions-hit rows for the Case Detail screen. */
export interface CaseDetail extends Case {
  id_document_url: string | null
  selfie_url: string | null
  extraction: Extraction | null
  face_match: FaceMatch | null
  sanctions_hits: SanctionsHit[]
}

export interface ReviewDecisionRequest {
  decision: ReviewDecision
  justification: string
}

/** Column shape for reviewers.* (Phase 9: GET/POST /reviewers) */
export interface Reviewer {
  id: string
  email: string
  role: ReviewerRole
  created_at: string
  revoked_at: string | null
}

/** Column shape for api_keys.* (Phase 9: GET/POST /api-keys) -- the raw key
 * is only ever present in the create response, exactly once. */
export interface ApiKey {
  id: string
  name: string
  created_at: string
  revoked_at: string | null
}

export interface ApiKeyCreateResponse extends ApiKey {
  raw_key: string
}

/** Column shape for webhooks.* (Phase 9: GET/POST /webhooks) */
export interface Webhook {
  id: string
  url: string
  created_at: string
  disabled_at: string | null
}

/** Column shape for webhook_deliveries.* (Phase 9: GET /webhooks/{id}/deliveries) */
export interface WebhookDelivery {
  id: string
  webhook_id: string
  case_id: string
  event_type: string
  status: WebhookDeliveryStatus
  attempt_count: number
  last_attempted_at: string | null
}

/** Phase 9: GET /dashboard/summary */
export interface DashboardSummary {
  status_counts: Record<CaseStatus, number>
  cases_last_30_days: { date: string; count: number }[]
  recent_activity: {
    case_id: string
    subject_name: string
    event: string
    at: string
  }[]
}

export interface ApiError {
  detail: string
}
