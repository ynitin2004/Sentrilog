import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import type { CaseStatus, DashboardSummary } from '@/types/api'
import { formatDate } from '@/lib/utils'

const STATUS_COLOR: Record<CaseStatus, string> = {
  pending: 'var(--color-status-pending)',
  processing: 'var(--color-status-processing)',
  needs_review: 'var(--color-status-needs-review)',
  approved: 'var(--color-status-approved)',
  rejected: 'var(--color-status-rejected)',
}

const STATUS_LABEL: Record<CaseStatus, string> = {
  pending: 'Pending',
  processing: 'Processing',
  needs_review: 'Needs Review',
  approved: 'Approved',
  rejected: 'Rejected',
}

export function CaseVolumeChart({ data }: { data: DashboardSummary['cases_last_30_days'] }) {
  return (
    <ResponsiveContainer width="100%" height={220}>
      <BarChart data={data} margin={{ left: -20 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" vertical={false} />
        <XAxis
          dataKey="date"
          tickFormatter={(d: string) => formatDate(d)}
          tick={{ fill: 'var(--color-text-subtle)', fontSize: 12 }}
          axisLine={{ stroke: 'var(--color-border)' }}
          tickLine={false}
        />
        <YAxis
          allowDecimals={false}
          tick={{ fill: 'var(--color-text-subtle)', fontSize: 12 }}
          axisLine={false}
          tickLine={false}
        />
        <Tooltip
          contentStyle={{
            background: 'var(--color-surface)',
            border: '1px solid var(--color-border)',
            borderRadius: 8,
            fontSize: 12,
          }}
          labelFormatter={(d) => (typeof d === 'string' ? formatDate(d) : d)}
        />
        <Bar dataKey="count" fill="var(--color-brand)" radius={[4, 4, 0, 0]} />
      </BarChart>
    </ResponsiveContainer>
  )
}

export function StatusBreakdownChart({ counts }: { counts: DashboardSummary['status_counts'] }) {
  const data = (Object.entries(counts) as [CaseStatus, number][])
    .filter(([, count]) => count > 0)
    .map(([status, count]) => ({ status, count }))

  return (
    <div className="flex items-center gap-6">
      <ResponsiveContainer width={140} height={140}>
        <PieChart>
          <Pie
            data={data}
            dataKey="count"
            nameKey="status"
            innerRadius={40}
            outerRadius={65}
            paddingAngle={2}
          >
            {data.map((entry) => (
              <Cell key={entry.status} fill={STATUS_COLOR[entry.status]} />
            ))}
          </Pie>
          <Tooltip
            contentStyle={{
              background: 'var(--color-surface)',
              border: '1px solid var(--color-border)',
              borderRadius: 8,
              fontSize: 12,
            }}
            formatter={(value, name) => [value, STATUS_LABEL[name as CaseStatus]]}
          />
        </PieChart>
      </ResponsiveContainer>
      <ul className="space-y-1.5 text-sm">
        {data.map((entry) => (
          <li key={entry.status} className="flex items-center gap-2">
            <span
              className="h-2.5 w-2.5 shrink-0 rounded-full"
              style={{ backgroundColor: STATUS_COLOR[entry.status] }}
              aria-hidden="true"
            />
            <span className="text-text-muted">{STATUS_LABEL[entry.status]}</span>
            <span className="text-text font-medium">{entry.count}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}
