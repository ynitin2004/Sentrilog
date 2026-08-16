import type {
  ApiKey,
  Case,
  CaseDetail,
  DashboardSummary,
  ReviewQueueCase,
  Reviewer,
  Webhook,
  WebhookDelivery,
} from '@/types/api'

// Deliberately fixed timestamps, not Date.now()-relative -- so a Storybook
// snapshot or a screenshot taken today looks the same as one taken next
// month, and so tests asserting on rendered dates are deterministic.
const DAY = 'T09:14:00Z'

export const mockReviewQueue: ReviewQueueCase[] = [
  {
    case_id: 'a1e6b1f0-1111-4a11-8a11-000000000001',
    subject_name: 'Amara Okafor',
    subject_dob: '1991-03-14',
    risk_score: 0.42,
    created_at: `2026-08-12${DAY}`,
    claimed_by_reviewer_id: null,
    claimed_at: null,
  },
  {
    case_id: 'a1e6b1f0-1111-4a11-8a11-000000000002',
    subject_name: 'Mohammed Al-Rashid',
    subject_dob: '1988-11-02',
    risk_score: 0.91,
    created_at: `2026-08-13${DAY}`,
    claimed_by_reviewer_id: null,
    claimed_at: null,
  },
  {
    case_id: 'a1e6b1f0-1111-4a11-8a11-000000000003',
    subject_name: 'Jane Doe',
    subject_dob: '1995-07-22',
    risk_score: 0.28,
    created_at: `2026-08-13${DAY}`,
    claimed_by_reviewer_id: 'rev-1',
    claimed_at: `2026-08-14${DAY}`,
  },
  {
    case_id: 'a1e6b1f0-1111-4a11-8a11-000000000004',
    subject_name: 'Yuki Tanaka',
    subject_dob: '1979-01-30',
    risk_score: null,
    created_at: `2026-08-14${DAY}`,
    claimed_by_reviewer_id: null,
    claimed_at: null,
  },
]

export const mockCases: Case[] = [
  ...mockReviewQueue.map((c) => ({
    case_id: c.case_id,
    status: 'needs_review' as const,
    subject_name: c.subject_name,
    subject_dob: c.subject_dob,
    decision: null,
    created_at: c.created_at,
    risk_score: c.risk_score,
  })),
  {
    case_id: 'a1e6b1f0-1111-4a11-8a11-000000000005',
    status: 'approved',
    subject_name: 'Priya Sharma',
    subject_dob: '1993-05-19',
    decision: 'approved',
    created_at: `2026-08-11${DAY}`,
    risk_score: 0.06,
  },
  {
    case_id: 'a1e6b1f0-1111-4a11-8a11-000000000006',
    status: 'rejected',
    subject_name: 'Carlos Mendes',
    subject_dob: '1985-09-08',
    decision: 'rejected',
    created_at: `2026-08-10${DAY}`,
    risk_score: 0.97,
  },
  {
    case_id: 'a1e6b1f0-1111-4a11-8a11-000000000007',
    status: 'processing',
    subject_name: 'Elena Petrova',
    subject_dob: '1990-12-01',
    decision: null,
    created_at: `2026-08-14${DAY}`,
    risk_score: null,
  },
]

export const mockCaseDetails: Record<string, CaseDetail> = {
  'a1e6b1f0-1111-4a11-8a11-000000000002': {
    case_id: 'a1e6b1f0-1111-4a11-8a11-000000000002',
    status: 'needs_review',
    subject_name: 'Mohammed Al-Rashid',
    subject_dob: '1988-11-02',
    decision: null,
    created_at: `2026-08-13${DAY}`,
    risk_score: 0.91,
    id_document_url: null,
    selfie_url: null,
    extraction: {
      document_id: 'doc-id-1',
      method: 'mrz',
      confidence: 0.98,
      valid: true,
      full_name: 'MOHAMMED AL-RASHID',
      date_of_birth: '1988-11-02',
      document_number: 'P1234567',
      nationality: 'ARE',
      expiry_date: '2029-04-11',
    },
    face_match: { similarity_score: 0.94, reason: null },
    sanctions_hits: [
      {
        list_source: 'SAMPLE-OFAC-SDN',
        matched_name: 'Mohammed Al-Rashid',
        match_score: 0.89,
        method: 'phonetic',
      },
    ],
  },
  'a1e6b1f0-1111-4a11-8a11-000000000001': {
    case_id: 'a1e6b1f0-1111-4a11-8a11-000000000001',
    status: 'needs_review',
    subject_name: 'Amara Okafor',
    subject_dob: '1991-03-14',
    decision: null,
    created_at: `2026-08-12${DAY}`,
    risk_score: 0.42,
    id_document_url: null,
    selfie_url: null,
    extraction: {
      document_id: 'doc-id-2',
      method: 'vlm',
      confidence: 0.62,
      valid: true,
      full_name: 'Amara Okafor',
      date_of_birth: '1991-03-14',
      document_number: 'NG7654321',
      nationality: 'NGA',
      expiry_date: '2027-02-01',
    },
    face_match: { similarity_score: 0.58, reason: null },
    sanctions_hits: [],
  },
}

export const mockReviewers: Reviewer[] = [
  {
    id: 'rev-1',
    email: 'aiko.tanaka@demo-tenant.example',
    role: 'reviewer',
    created_at: `2026-07-21${DAY}`,
    revoked_at: null,
  },
  {
    id: 'rev-2',
    email: 'marcus.reid@demo-tenant.example',
    role: 'admin',
    created_at: `2026-07-21${DAY}`,
    revoked_at: null,
  },
  {
    id: 'rev-3',
    email: 'former.contractor@demo-tenant.example',
    role: 'reviewer',
    created_at: `2026-06-01${DAY}`,
    revoked_at: `2026-07-15${DAY}`,
  },
]

export const mockApiKeys: ApiKey[] = [
  {
    id: 'key-1',
    name: 'production-intake',
    created_at: `2026-07-20${DAY}`,
    revoked_at: null,
  },
  {
    id: 'key-2',
    name: 'staging-smoke-test',
    created_at: `2026-08-01${DAY}`,
    revoked_at: null,
  },
]

export const mockWebhooks: Webhook[] = [
  {
    id: 'wh-1',
    url: 'https://demo-tenant.example/webhooks/sentrilog',
    created_at: `2026-07-22${DAY}`,
    disabled_at: null,
  },
]

export const mockWebhookDeliveries: WebhookDelivery[] = [
  {
    id: 'del-1',
    webhook_id: 'wh-1',
    case_id: 'a1e6b1f0-1111-4a11-8a11-000000000005',
    event_type: 'case.decided',
    status: 'delivered',
    attempt_count: 1,
    last_attempted_at: `2026-08-11${DAY}`,
  },
  {
    id: 'del-2',
    webhook_id: 'wh-1',
    case_id: 'a1e6b1f0-1111-4a11-8a11-000000000006',
    event_type: 'case.decided',
    status: 'failed',
    attempt_count: 3,
    last_attempted_at: `2026-08-10${DAY}`,
  },
]

export const mockDashboardSummary: DashboardSummary = {
  status_counts: {
    pending: 1,
    processing: 1,
    needs_review: 4,
    approved: 18,
    rejected: 3,
  },
  cases_last_30_days: [
    { date: '2026-07-16', count: 2 },
    { date: '2026-07-23', count: 4 },
    { date: '2026-07-30', count: 3 },
    { date: '2026-08-06', count: 6 },
    { date: '2026-08-13', count: 5 },
  ],
  recent_activity: [
    {
      case_id: 'a1e6b1f0-1111-4a11-8a11-000000000005',
      subject_name: 'Priya Sharma',
      event: 'approved by aiko.tanaka@demo-tenant.example',
      at: `2026-08-11${DAY}`,
    },
    {
      case_id: 'a1e6b1f0-1111-4a11-8a11-000000000006',
      subject_name: 'Carlos Mendes',
      event: 'rejected by marcus.reid@demo-tenant.example',
      at: `2026-08-10${DAY}`,
    },
    {
      case_id: 'a1e6b1f0-1111-4a11-8a11-000000000004',
      subject_name: 'Yuki Tanaka',
      event: 'case created',
      at: `2026-08-14${DAY}`,
    },
  ],
}
