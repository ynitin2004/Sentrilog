import { Inbox } from 'lucide-react'
import { DataTable, type DataTableColumn } from '@/components/ui/data-table'
import { Card, CardContent } from '@/components/ui/card'
import { RiskScoreBar } from '@/components/domain/risk-score-gauge'
import { formatDate } from '@/lib/utils'
import type { ReviewQueueCase } from '@/types/api'

export interface ReviewQueueListProps {
  cases: ReviewQueueCase[]
  isLoading?: boolean
  onSelect: (caseItem: ReviewQueueCase) => void
}

const columns: DataTableColumn<ReviewQueueCase>[] = [
  {
    key: 'subject',
    header: 'Subject',
    render: (c) => <span className="font-medium">{c.subject_name}</span>,
  },
  { key: 'dob', header: 'Date of birth', render: (c) => c.subject_dob ?? '—' },
  { key: 'risk', header: 'Risk score', render: (c) => <RiskScoreBar score={c.risk_score} /> },
  { key: 'created', header: 'Submitted', render: (c) => formatDate(c.created_at) },
  {
    key: 'claimed',
    header: 'Claimed by',
    render: (c) =>
      c.claimed_by_reviewer_id ? 'Claimed' : <span className="text-text-subtle">Unclaimed</span>,
  },
]

/** Desktop table + mobile card list for the same data -- Tailwind's `hidden md:block` /
 * `md:hidden` pair, not a horizontal-scroll fallback, so the queue is genuinely usable on a
 * phone-sized viewport (a reviewer triaging from their phone is a real scenario for this
 * product, not a hypothetical). */
export function ReviewQueueList({ cases, isLoading = false, onSelect }: ReviewQueueListProps) {
  return (
    <>
      <div className="hidden md:block">
        <DataTable
          columns={columns}
          rows={cases}
          getRowKey={(c) => c.case_id}
          isLoading={isLoading}
          emptyIcon={Inbox}
          emptyTitle="No cases awaiting review"
          emptyDescription="New ambiguous cases will appear here as they're parked by the pipeline."
          onRowClick={onSelect}
        />
      </div>
      <div className="space-y-3 md:hidden">
        {isLoading ? (
          Array.from({ length: 3 }).map((_, i) => (
            <Card key={i} className="animate-pulse">
              <CardContent className="h-24" />
            </Card>
          ))
        ) : cases.length === 0 ? (
          <Card>
            <CardContent className="flex flex-col items-center gap-2 py-10 text-center">
              <Inbox className="text-text-subtle h-6 w-6" aria-hidden="true" />
              <p className="text-sm font-medium">No cases awaiting review</p>
            </CardContent>
          </Card>
        ) : (
          cases.map((c) => (
            <button
              key={c.case_id}
              type="button"
              onClick={() => onSelect(c)}
              className="w-full text-left focus-visible:outline-2 focus-visible:outline-[var(--color-brand)]"
            >
              <Card>
                <CardContent className="space-y-2">
                  <div className="flex items-center justify-between">
                    <p className="text-text font-medium">{c.subject_name}</p>
                    <span className="text-text-subtle text-xs">{formatDate(c.created_at)}</span>
                  </div>
                  <RiskScoreBar score={c.risk_score} />
                  <p className="text-text-subtle text-xs">
                    {c.claimed_by_reviewer_id ? 'Claimed' : 'Unclaimed'}
                  </p>
                </CardContent>
              </Card>
            </button>
          ))
        )}
      </div>
    </>
  )
}
