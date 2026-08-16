import { useNavigate, useParams } from 'react-router-dom'
import { ArrowLeft, ShieldAlert, UserCheck } from 'lucide-react'
import { AppShell } from '@/components/domain/app-shell'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { DocumentPreview } from '@/components/domain/document-preview'
import { RiskScoreGauge } from '@/components/domain/risk-score-gauge'
import { DecisionPanel } from '@/components/domain/decision-panel'
import { useToast } from '@/components/ui/toast'
import { useCaseDetail, useReviewQueue } from '@/hooks/use-mock-data'
import { formatDate } from '@/lib/utils'
import type { ReviewDecision } from '@/types/api'

export function ReviewerCaseDetailPage() {
  const { caseId } = useParams<{ caseId: string }>()
  const { data: detail, isLoading } = useCaseDetail(caseId)
  const { claim, decide } = useReviewQueue()
  const { toast } = useToast()
  const navigate = useNavigate()

  const handleClaim = () => {
    if (!caseId) return
    claim(caseId)
    toast({ title: 'Case claimed' })
  }

  const handleDecision = async (decision: ReviewDecision, justification: string) => {
    if (!caseId) return
    await new Promise((r) => setTimeout(r, 500))
    decide(caseId, decision)
    toast({
      title: `Decision recorded: ${decision}`,
      description: justification,
    })
    if (decision !== 'escalated') navigate('/reviewer/queue')
  }

  return (
    <AppShell persona="reviewer">
      <div className="mx-auto max-w-4xl space-y-6">
        <Button variant="ghost" size="sm" onClick={() => navigate('/reviewer/queue')}>
          <ArrowLeft className="h-4 w-4" /> Back to queue
        </Button>

        {isLoading ? (
          <div className="space-y-4">
            <Skeleton className="h-8 w-64" />
            <Skeleton className="h-64 w-full" />
          </div>
        ) : !detail ? (
          <Card>
            <CardContent className="text-text-subtle py-10 text-center text-sm">
              Case not found. It may have already been decided.
            </CardContent>
          </Card>
        ) : (
          <>
            <div className="flex flex-wrap items-start justify-between gap-4">
              <div>
                <h1 className="text-text text-xl font-semibold">{detail.subject_name}</h1>
                <p className="text-text-subtle text-sm">
                  DOB {detail.subject_dob ?? '—'} · Submitted {formatDate(detail.created_at)}
                </p>
              </div>
              <Button variant="secondary" onClick={handleClaim}>
                <UserCheck className="h-4 w-4" /> Claim case
              </Button>
            </div>

            {detail.sanctions_hits.length > 0 && (
              <div className="border-status-rejected-bg bg-status-rejected-bg text-status-rejected flex items-start gap-2 rounded-lg border px-4 py-3">
                <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
                <p className="text-sm">
                  {detail.sanctions_hits.length} sanctions list{' '}
                  {detail.sanctions_hits.length === 1 ? 'hit' : 'hits'} found -- reviewed closely
                  below.
                </p>
              </div>
            )}

            <div className="grid gap-6 lg:grid-cols-3">
              <div className="space-y-6 lg:col-span-2">
                <Card>
                  <CardHeader>
                    <CardTitle>Extracted identity</CardTitle>
                  </CardHeader>
                  <CardContent>
                    {detail.extraction ? (
                      <dl className="grid grid-cols-2 gap-x-4 gap-y-3 text-sm">
                        <Field label="Full name" value={detail.extraction.full_name} />
                        <Field label="Date of birth" value={detail.extraction.date_of_birth} />
                        <Field label="Document number" value={detail.extraction.document_number} />
                        <Field label="Nationality" value={detail.extraction.nationality} />
                        <Field label="Expiry date" value={detail.extraction.expiry_date} />
                        <Field
                          label="Extraction method"
                          value={detail.extraction.method === 'mrz' ? 'MRZ (ICAO 9303)' : 'VLM'}
                        />
                      </dl>
                    ) : (
                      <p className="text-text-subtle text-sm">
                        Extraction failed or is still pending.
                      </p>
                    )}
                  </CardContent>
                </Card>

                <Card>
                  <CardHeader>
                    <CardTitle>Documents</CardTitle>
                  </CardHeader>
                  <CardContent>
                    <DocumentPreview
                      idDocumentUrl={detail.id_document_url}
                      selfieUrl={detail.selfie_url}
                    />
                  </CardContent>
                </Card>

                <Card>
                  <CardHeader>
                    <CardTitle>Sanctions screening</CardTitle>
                  </CardHeader>
                  <CardContent>
                    {detail.sanctions_hits.length === 0 ? (
                      <p className="text-text-subtle text-sm">No sanctions list matches.</p>
                    ) : (
                      <ul className="space-y-2">
                        {detail.sanctions_hits.map((hit, i) => (
                          <li
                            key={i}
                            className="border-border flex items-center justify-between rounded-md border px-3 py-2 text-sm"
                          >
                            <div>
                              <p className="text-text font-medium">{hit.matched_name}</p>
                              <p className="text-text-subtle text-xs">{hit.list_source}</p>
                            </div>
                            <Badge variant="danger">
                              {hit.method} · {hit.match_score.toFixed(2)}
                            </Badge>
                          </li>
                        ))}
                      </ul>
                    )}
                  </CardContent>
                </Card>

                <DecisionPanel onSubmit={handleDecision} />
              </div>

              <div className="space-y-6">
                <Card>
                  <CardHeader>
                    <CardTitle>Risk score</CardTitle>
                  </CardHeader>
                  <CardContent>
                    <RiskScoreGauge score={detail.risk_score} />
                    <dl className="mt-4 space-y-2 text-xs">
                      <div className="flex justify-between">
                        <dt className="text-text-subtle">Extraction confidence</dt>
                        <dd className="text-text font-mono">
                          {detail.extraction ? detail.extraction.confidence.toFixed(2) : '—'}
                        </dd>
                      </div>
                      <div className="flex justify-between">
                        <dt className="text-text-subtle">Face match score</dt>
                        <dd className="text-text font-mono">
                          {detail.face_match?.similarity_score?.toFixed(2) ?? '—'}
                        </dd>
                      </div>
                      <div className="flex justify-between">
                        <dt className="text-text-subtle">Sanctions hits</dt>
                        <dd className="text-text font-mono">{detail.sanctions_hits.length}</dd>
                      </div>
                    </dl>
                  </CardContent>
                </Card>
              </div>
            </div>
          </>
        )}
      </div>
    </AppShell>
  )
}

function Field({ label, value }: { label: string; value: string | null }) {
  return (
    <div>
      <dt className="text-text-subtle text-xs">{label}</dt>
      <dd className="text-text">{value ?? '—'}</dd>
    </div>
  )
}
