import { CheckCircle2, Clock, FolderOpen, ShieldAlert, XCircle } from 'lucide-react'
import { AppShell } from '@/components/domain/app-shell'
import { StatCard } from '@/components/domain/stat-card'
import { CaseVolumeChart, StatusBreakdownChart } from '@/components/domain/charts'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { useDashboardSummary } from '@/hooks/use-mock-data'
import { formatDateTime } from '@/lib/utils'

export function AdminOverviewPage() {
  const { data, isLoading } = useDashboardSummary()
  const counts = data.status_counts

  return (
    <AppShell persona="admin">
      <div className="mx-auto max-w-5xl space-y-6">
        <div>
          <h1 className="text-text text-xl font-semibold">Overview</h1>
          <p className="text-text-subtle text-sm">Case volume and status across your tenant.</p>
        </div>

        <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
          <StatCard
            label="Needs review"
            value={counts.needs_review}
            icon={ShieldAlert}
            isLoading={isLoading}
            tone="warning"
          />
          <StatCard
            label="Processing"
            value={counts.processing + counts.pending}
            icon={Clock}
            isLoading={isLoading}
            tone="brand"
          />
          <StatCard
            label="Approved"
            value={counts.approved}
            icon={CheckCircle2}
            isLoading={isLoading}
          />
          <StatCard
            label="Rejected"
            value={counts.rejected}
            icon={XCircle}
            isLoading={isLoading}
            tone="danger"
          />
        </div>

        <div className="grid gap-6 lg:grid-cols-3">
          <Card className="lg:col-span-2">
            <CardHeader>
              <CardTitle>Cases, last 30 days</CardTitle>
            </CardHeader>
            <CardContent>
              <CaseVolumeChart data={data.cases_last_30_days} />
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle>Status breakdown</CardTitle>
            </CardHeader>
            <CardContent>
              <StatusBreakdownChart counts={counts} />
            </CardContent>
          </Card>
        </div>

        <Card>
          <CardHeader>
            <CardTitle>Recent activity</CardTitle>
          </CardHeader>
          <CardContent>
            {data.recent_activity.length === 0 ? (
              <div className="text-text-subtle flex flex-col items-center gap-2 py-8 text-center">
                <FolderOpen className="h-6 w-6" aria-hidden="true" />
                <p className="text-sm">No activity yet.</p>
              </div>
            ) : (
              <ul className="divide-border divide-y">
                {data.recent_activity.map((item) => (
                  <li
                    key={`${item.case_id}-${item.at}`}
                    className="flex items-center justify-between py-2.5 text-sm"
                  >
                    <div>
                      <p className="text-text font-medium">{item.subject_name}</p>
                      <p className="text-text-subtle text-xs">{item.event}</p>
                    </div>
                    <span className="text-text-subtle text-xs">{formatDateTime(item.at)}</span>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
      </div>
    </AppShell>
  )
}
