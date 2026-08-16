import { AppShell } from '@/components/domain/app-shell'
import { ReviewersTable } from '@/components/domain/admin-tables'
import { AddReviewerForm } from '@/components/domain/add-reviewer-form'
import { useToast } from '@/components/ui/toast'
import { useReviewers } from '@/hooks/use-mock-data'
import type { Reviewer } from '@/types/api'

export function AdminReviewersPage() {
  const { data: reviewers, isLoading, create, revoke } = useReviewers()
  const { toast } = useToast()

  const handleRevoke = (reviewer: Reviewer) => {
    revoke(reviewer.id)
    toast({ title: `Revoked access for ${reviewer.email}` })
  }

  return (
    <AppShell persona="admin">
      <div className="mx-auto max-w-4xl space-y-6">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <h1 className="text-text text-xl font-semibold">Reviewers</h1>
            <p className="text-text-subtle text-sm">Who can work the review queue.</p>
          </div>
          <AddReviewerForm onCreate={create} />
        </div>
        <ReviewersTable reviewers={reviewers} isLoading={isLoading} onRevoke={handleRevoke} />
      </div>
    </AppShell>
  )
}
