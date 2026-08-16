import { useNavigate } from 'react-router-dom'
import { AppShell } from '@/components/domain/app-shell'
import { ReviewQueueList } from '@/components/domain/review-queue-list'
import { useReviewQueue } from '@/hooks/use-mock-data'

export function ReviewerQueuePage() {
  const { data: cases, isLoading } = useReviewQueue()
  const navigate = useNavigate()

  return (
    <AppShell persona="reviewer">
      <div className="mx-auto max-w-5xl space-y-6">
        <div>
          <h1 className="text-text text-xl font-semibold">Review queue</h1>
          <p className="text-text-subtle text-sm">
            Cases the pipeline couldn't auto-clear -- {cases.length} awaiting a decision.
          </p>
        </div>
        <ReviewQueueList
          cases={cases}
          isLoading={isLoading}
          onSelect={(c) => navigate(`/reviewer/cases/${c.case_id}`)}
        />
      </div>
    </AppShell>
  )
}
